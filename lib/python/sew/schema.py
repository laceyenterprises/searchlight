"""Versioned SEW manifest and record schema validators.

The foundation layer is intentionally strict: unknown keys and ambiguous
telemetry fail during fixture validation instead of drifting into reports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import yaml

SCHEMA_VERSION = 1
PROVIDERS = frozenset(
    {
        "native",
        "no-search",
        "floor",
        "ceiling",
        "exa",
        "parallel-web",
        "firecrawl",
        "brave",
        "tavily",
        "perplexity",
        "fixture",
    }
)
HARNESSES = frozenset({"codex", "claude-code", "pi", "fixture"})
TASK_CLASSES = frozenset(
    {
        "fact_lookup",
        "freshness",
        "docs_qa",
        "deep_crawl",
        "multi_source_synthesis",
        "code_research",
        "adversarial_source_hygiene",
        "js_rendered_content",
        "no_reliable_answer",
    }
)
RUN_STATUSES = frozenset(
    {
        "succeeded",
        "failed",
        "timeout",
        "unsupported",
        "not_applicable",
        "cancelled",
        "harness_boot_failed",
        "provider_unavailable",
        "budget_exhausted",
        "contaminated",
    }
)
PROVIDER_CALL_STATUSES = frozenset(
    {"ok", "failed", "rate_limited", "timeout", "refused", "not_applicable"}
)
EVALUATION_OUTCOMES = frozenset({"pass", "fail", "not_applicable"})
TOKEN_ACCOUNTING_SOURCES = frozenset({"measured", "estimated", "unknown"})
TOOL_CALLING_MODES = frozenset({"native", "prompt_wrapped", "unsupported"})
PROVIDER_COMPRESSION_MODES = frozenset({"none", "truncate", "summarize", "unknown"})

_SUITE_TIMEOUT_KEYS = frozenset({"run_seconds", "provider_call_seconds"})
_SUITE_BUDGET_KEYS = frozenset(
    {"max_provider_calls", "max_provider_result_chars", "max_total_tokens"}
)
_RETRY_KEYS = frozenset({"max_attempts"})
_SUITE_KEYS = frozenset(
    {
        "schema_version",
        "suite_id",
        "version",
        "description",
        "randomization_seed",
        "fixture_mode",
        "repetitions",
        "timeouts",
        "budgets",
        "retries",
        "providers",
        "harnesses",
        "tasks",
    }
)
_TASK_KEYS = frozenset(
    {
        "schema_version",
        "task_id",
        "version",
        "title",
        "task_class",
        "created_at",
        "live_web_required",
        "fixture_mode_allowed",
        "freshness_window_days",
        "allowed_domains",
        "disallowed_domains",
        "expected_output_schema",
        "deterministic_validator",
        "blinded_judge",
        "budgets",
        "retries",
        "success_criteria",
    }
)
_EXPECTED_OUTPUT_SCHEMA_KEYS = frozenset({"type", "required"})
_DETERMINISTIC_VALIDATOR_KEYS = frozenset(
    {
        "kind",
        "expected_answer",
        "expected_answerable",
        "expected_fields",
        "forbidden_claims",
        "required_source_ids",
        "stale_source_ids",
    }
)
_BLINDED_JUDGE_KEYS = frozenset(
    {"rubric_ref", "judge_prompt_blinded", "minimum_score", "score_scale"}
)
_SCORE_SCALE_KEYS = frozenset({"min", "max"})
_TASK_BUDGET_KEYS = frozenset(
    {"max_provider_calls", "max_pages", "max_bytes", "max_total_tokens", "wall_clock_seconds"}
)
_RUN_KEYS = frozenset(
    {
        "schema_version",
        "run_id",
        "suite_id",
        "task_id",
        "task_source",
        "provider_id",
        "harness_id",
        "model_profile",
        "mode",
        "status",
        "started_at",
        "ended_at",
        "metrics_ref",
        "evaluation_ref",
        "evidence_bundle_ref",
        "harness_ref",
        "provider_call_refs",
        "failure_category",
        "provider_availability",
    }
)
_METRICS_KEYS = frozenset(
    {
        "schema_version",
        "run_id",
        "timestamps",
        "latency_ms",
        "token_usage",
        "provider_calls",
        "provider_call_records",
        "provider_result_context",
        "provider_result_chars",
        "source_counts",
        "source_use",
        "failure_categories",
        "cost",
    }
)
_TIMESTAMP_KEYS = frozenset(
    {
        "queued_at",
        "started_at",
        "harness_ready_at",
        "prompt_sent_at",
        "provider_call_first_started_at",
        "provider_call_last_ended_at",
        "first_source_at",
        "first_output_token_at",
        "final_answer_at",
        "evaluator_started_at",
        "evaluator_ended_at",
        "completed_at",
    }
)
_LATENCY_MS_KEYS = frozenset(
    {
        "end_to_end",
        "provider",
        "harness",
        "evaluator",
        "total_wall",
        "harness_boot",
        "provider_total",
        "post_provider_synthesis",
        "timeout_debt",
        "unavailable",
    }
)
_TOKEN_USAGE_KEYS = frozenset(
    {
        "accounting_source",
        "source_kind",
        "input",
        "cached_input",
        "cache_write",
        "cache_write_5m",
        "cache_write_1h",
        "output",
        "reasoning",
        "total_billable",
    }
)
_PROVIDER_CALL_METRIC_KEYS = PROVIDER_CALL_STATUSES | frozenset(
    {"total", "retry_count", "latency_ms_total"}
)
_PROVIDER_CALL_RECORD_KEYS = frozenset(
    {
        "call_id",
        "provider_id",
        "operation",
        "status",
        "started_at",
        "ended_at",
        "latency_ms",
        "retry_count",
        "error_class",
        "source_ref_count",
    }
)
_PROVIDER_RESULT_CONTEXT_KEYS = frozenset(
    {"result_chars", "injected_chars", "compression_ratio", "compression_mode"}
)
_SOURCE_COUNTS_KEYS = frozenset({"normalized", "cited"})
_SOURCE_USE_KEYS = frozenset(
    {
        "normalized",
        "cited",
        "distinct_domains",
        "primary_source_count",
        "reachable",
        "duplicate_or_mirror",
    }
)
_COST_KEYS = frozenset({"currency", "amount", "source"})
_EVALUATION_KEYS = frozenset(
    {
        "execution",
        "schema_version",
        "run_id",
        "task_id",
        "outcome",
        "dimensions",
        "failure_reasons",
        "judge",
        "evidence_refs",
    }
)
_EVALUATION_DIMENSION_KEYS = frozenset(
    {"completed", "correct", "grounded", "fresh", "schema_valid", "safe"}
)
_EVALUATION_JUDGE_KEYS = frozenset({"kind", "validator", "rubric_ref", "model_profile"})
_SOURCE_KEYS = frozenset(
    {
        "schema_version",
        "source_id",
        "provider_id",
        "url",
        "title",
        "snippet",
        "published_at",
        "modified_at",
        "retrieved_at",
        "content_hash",
        "content_length",
        "redaction",
    }
)
_SOURCE_REDACTION_KEYS = frozenset({"state", "raw_content_included"})
_PROVIDER_CALL_KEYS = frozenset(
    {
        "schema_version",
        "call_id",
        "run_id",
        "provider_id",
        "operation",
        "status",
        "started_at",
        "ended_at",
        "request",
        "response",
        "normalized_source_refs",
        "retry_count",
        "error_class",
    }
)
_EVIDENCE_KEYS = frozenset(
    {
        "schema_version",
        "bundle_id",
        "run_id",
        "redaction",
        "artifacts",
        "records",
    }
)
_EVIDENCE_REDACTION_KEYS = frozenset(
    {
        "raw_transcripts_included",
        "credentials_included",
        "cookies_included",
        "unrestricted_page_archives_included",
    }
)
_EVIDENCE_ARTIFACT_KEYS = frozenset({"path", "media_type", "size_bytes", "redaction"})
_EVIDENCE_RECORDS_KEYS = frozenset(
    {"run", "metrics", "evaluation", "harness", "provider_calls", "normalized_sources"}
)
_HARNESS_RECORD_KEYS = frozenset(
    {
        "schema_version",
        "run_id",
        "harness_id",
        "profile_id",
        "resolved_model",
        "tool_calling_mode",
        "context_window_tokens",
        "provider_result_compression",
        "token_accounting_source",
        "provider_adapter_exposure",
        "fixture_mode",
    }
)
_PROVIDER_RESULT_COMPRESSION_KEYS = frozenset({"mode", "max_chars", "preserves_source_refs"})
_PROVIDER_ADAPTER_EXPOSURE_KEYS = frozenset(
    {"exposed", "provider_ids", "fixture_replay", "native_search_available"}
)


class SchemaError(ValueError):
    """Raised when a SEW manifest or fixture record violates its schema."""


@dataclass(frozen=True)
class FixtureValidation:
    run_id: str
    status: str
    metrics_ref: str
    evaluation_ref: str


def load_document(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaError(f"cannot read {path}: {exc}") from exc
    try:
        if path.suffix == ".json":
            return json.loads(text)
        return yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise SchemaError(f"{path}: invalid document: {exc}") from exc


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaError(f"{where} must be an object, got {type(value).__name__}")
    return value


def _list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise SchemaError(f"{where} must be an array, got {type(value).__name__}")
    return value


def _reject_unknown(data: dict[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise SchemaError(f"{where}: unknown key(s) {unknown}; allowed: {sorted(allowed)}")


def _required_str(data: dict[str, Any], key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{where}.{key} is required and must be a non-empty string")
    return value


def _optional_str(data: dict[str, Any], key: str, where: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{where}.{key} must be a non-empty string when present")
    return value


def _optional_str_or_null(data: dict[str, Any], key: str, where: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise SchemaError(f"{where}.{key} must be a string or null")
    return value


def _required_bool(data: dict[str, Any], key: str, where: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise SchemaError(f"{where}.{key} must be a boolean")
    return value


def _required_non_negative_int(data: dict[str, Any], key: str, where: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SchemaError(f"{where}.{key} must be a non-negative integer")
    return value


def _optional_non_negative_int_or_null(data: dict[str, Any], key: str, where: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SchemaError(f"{where}.{key} must be a non-negative integer or null")
    return value


def _optional_non_negative_number_or_null(
    data: dict[str, Any], key: str, where: str
) -> int | float | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise SchemaError(f"{where}.{key} must be a non-negative number or null")
    return value


def _optional_ratio_or_null(data: dict[str, Any], key: str, where: str) -> int | float | None:
    return _optional_non_negative_number_or_null(data, key, where)


def _str_list(value: Any, where: str, *, allow_empty: bool = True) -> list[str]:
    items = _list(value, where)
    if (not allow_empty and not items) or not all(isinstance(item, str) and item for item in items):
        raise SchemaError(f"{where} must contain non-empty strings")
    return items


def _schema_version(data: dict[str, Any], where: str) -> None:
    if data.get("schema_version") != SCHEMA_VERSION:
        raise SchemaError(f"{where}.schema_version must be {SCHEMA_VERSION}")


def _closed(value: str, allowed: frozenset[str], where: str) -> None:
    if value not in allowed:
        raise SchemaError(f"{where} must be one of {sorted(allowed)}, got {value!r}")


def validate_artifact_path(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{where} must be a non-empty relative path")
    if "\\" in value:
        raise SchemaError(f"{where} must use POSIX separators")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value.startswith("~")
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise SchemaError(f"{where} is unsafe; use a normalized relative path below the bundle")
    return str(path)


def validate_suite_manifest(data: Any) -> dict[str, Any]:
    doc = _mapping(data, "suite manifest")
    _reject_unknown(doc, _SUITE_KEYS, "suite manifest")
    _schema_version(doc, "suite manifest")
    _required_str(doc, "suite_id", "suite manifest")
    _required_str(doc, "version", "suite manifest")
    _required_non_negative_int(doc, "randomization_seed", "suite manifest")
    _required_bool(doc, "fixture_mode", "suite manifest")
    _required_non_negative_int(doc, "repetitions", "suite manifest")
    timeouts = _mapping(doc.get("timeouts"), "suite manifest.timeouts")
    _reject_unknown(timeouts, _SUITE_TIMEOUT_KEYS, "suite manifest.timeouts")
    for key in _SUITE_TIMEOUT_KEYS:
        _required_non_negative_int(timeouts, key, "suite manifest.timeouts")
    budgets = _mapping(doc.get("budgets"), "suite manifest.budgets")
    _reject_unknown(budgets, _SUITE_BUDGET_KEYS, "suite manifest.budgets")
    for key in _SUITE_BUDGET_KEYS:
        _required_non_negative_int(budgets, key, "suite manifest.budgets")
    if "retries" in doc:
        retries = _mapping(doc["retries"], "suite manifest.retries")
        _reject_unknown(retries, _RETRY_KEYS, "suite manifest.retries")
        _required_non_negative_int(retries, "max_attempts", "suite manifest.retries")
    providers = _list(doc.get("providers"), "suite manifest.providers")
    for provider in providers:
        if not isinstance(provider, str):
            raise SchemaError("suite manifest.providers entries must be strings")
        _closed(provider, PROVIDERS - {"fixture"}, "suite manifest.providers[]")
    harnesses = _mapping(doc.get("harnesses"), "suite manifest.harnesses")
    for harness, config in harnesses.items():
        _closed(harness, HARNESSES - {"fixture"}, "suite manifest.harnesses key")
        profiles = _list(
            _mapping(config, f"suite manifest.harnesses.{harness}").get("model_profiles"),
            f"suite manifest.harnesses.{harness}.model_profiles",
        )
        if not profiles or not all(isinstance(item, str) and item for item in profiles):
            raise SchemaError(
                f"suite manifest.harnesses.{harness}.model_profiles must be non-empty strings"
            )
    _str_list(doc.get("tasks"), "suite manifest.tasks", allow_empty=False)
    return doc


def validate_task_manifest(data: Any) -> dict[str, Any]:
    doc = _mapping(data, "task manifest")
    _reject_unknown(doc, _TASK_KEYS, "task manifest")
    _schema_version(doc, "task manifest")
    _required_str(doc, "task_id", "task manifest")
    _required_str(doc, "version", "task manifest")
    _required_str(doc, "title", "task manifest")
    _required_str(doc, "created_at", "task manifest")
    _closed(
        _required_str(doc, "task_class", "task manifest"), TASK_CLASSES, "task manifest.task_class"
    )
    for key in ("live_web_required", "fixture_mode_allowed"):
        _required_bool(doc, key, "task manifest")
    if "freshness_window_days" in doc:
        _required_non_negative_int(doc, "freshness_window_days", "task manifest")
    _str_list(doc.get("allowed_domains"), "task manifest.allowed_domains")
    _str_list(doc.get("disallowed_domains"), "task manifest.disallowed_domains")
    if "deterministic_validator" not in doc and "blinded_judge" not in doc:
        raise SchemaError("task manifest must declare deterministic_validator or blinded_judge")
    output = _mapping(doc.get("expected_output_schema"), "task manifest.expected_output_schema")
    _reject_unknown(output, _EXPECTED_OUTPUT_SCHEMA_KEYS, "task manifest.expected_output_schema")
    _closed(
        _required_str(output, "type", "task manifest.expected_output_schema"),
        frozenset({"object"}),
        "task manifest.expected_output_schema.type",
    )
    _str_list(
        output.get("required"), "task manifest.expected_output_schema.required", allow_empty=False
    )
    if "deterministic_validator" in doc:
        validator = _mapping(
            doc["deterministic_validator"], "task manifest.deterministic_validator"
        )
        _reject_unknown(
            validator, _DETERMINISTIC_VALIDATOR_KEYS, "task manifest.deterministic_validator"
        )
        _required_str(validator, "kind", "task manifest.deterministic_validator")
        if "expected_fields" in validator:
            expected_fields = _mapping(
                validator["expected_fields"],
                "task manifest.deterministic_validator.expected_fields",
            )
            for field, value in expected_fields.items():
                if not isinstance(field, str) or not field:
                    raise SchemaError(
                        "task manifest.deterministic_validator.expected_fields keys must be strings"
                    )
                if not isinstance(value, str) or not value:
                    raise SchemaError(
                        "task manifest.deterministic_validator.expected_fields values must be strings"
                    )
        for key in ("required_source_ids", "stale_source_ids", "forbidden_claims"):
            if key in validator:
                _str_list(validator[key], f"task manifest.deterministic_validator.{key}")
        if "expected_answerable" in validator:
            _required_bool(
                validator, "expected_answerable", "task manifest.deterministic_validator"
            )
        if "expected_answer" in validator:
            _required_str(validator, "expected_answer", "task manifest.deterministic_validator")
    if "blinded_judge" in doc:
        judge = _mapping(doc["blinded_judge"], "task manifest.blinded_judge")
        _reject_unknown(judge, _BLINDED_JUDGE_KEYS, "task manifest.blinded_judge")
        _required_str(judge, "rubric_ref", "task manifest.blinded_judge")
        _required_bool(judge, "judge_prompt_blinded", "task manifest.blinded_judge")
        _required_non_negative_int(judge, "minimum_score", "task manifest.blinded_judge")
        scale = _mapping(judge.get("score_scale"), "task manifest.blinded_judge.score_scale")
        _reject_unknown(scale, _SCORE_SCALE_KEYS, "task manifest.blinded_judge.score_scale")
        for key in _SCORE_SCALE_KEYS:
            _required_non_negative_int(scale, key, "task manifest.blinded_judge.score_scale")
    budgets = _mapping(doc.get("budgets"), "task manifest.budgets")
    _reject_unknown(budgets, _TASK_BUDGET_KEYS, "task manifest.budgets")
    for key in _TASK_BUDGET_KEYS:
        _required_non_negative_int(budgets, key, "task manifest.budgets")
    if "retries" in doc:
        retries = _mapping(doc["retries"], "task manifest.retries")
        _reject_unknown(retries, _RETRY_KEYS, "task manifest.retries")
        _required_non_negative_int(retries, "max_attempts", "task manifest.retries")
    _str_list(doc.get("success_criteria"), "task manifest.success_criteria", allow_empty=False)
    return doc


def validate_run_record(data: Any) -> dict[str, Any]:
    doc = _mapping(data, "run record")
    _reject_unknown(doc, _RUN_KEYS, "run record")
    _schema_version(doc, "run record")
    _required_str(doc, "run_id", "run record")
    _required_str(doc, "suite_id", "run record")
    _required_str(doc, "task_id", "run record")
    _closed(
        doc.get("task_source", "legacy"),
        frozenset({"legacy", "production", "gap"}),
        "run record.task_source",
    )
    _closed(_required_str(doc, "provider_id", "run record"), PROVIDERS, "run record.provider_id")
    _closed(_required_str(doc, "harness_id", "run record"), HARNESSES, "run record.harness_id")
    _required_str(doc, "model_profile", "run record")
    _required_str(doc, "mode", "run record")
    _closed(_required_str(doc, "status", "run record"), RUN_STATUSES, "run record.status")
    _required_str(doc, "started_at", "run record")
    _required_str(doc, "ended_at", "run record")
    _optional_str(doc, "failure_category", "run record")
    if "provider_availability" in doc:
        _closed(
            _required_str(doc, "provider_availability", "run record"),
            frozenset({"available", "unavailable", "unknown"}),
            "run record.provider_availability",
        )
    for ref in ("metrics_ref", "evaluation_ref", "evidence_bundle_ref"):
        validate_artifact_path(_required_str(doc, ref, "run record"), f"run record.{ref}")
    if "harness_ref" in doc:
        validate_artifact_path(
            _required_str(doc, "harness_ref", "run record"), "run record.harness_ref"
        )
    for ref in _list(doc.get("provider_call_refs", []), "run record.provider_call_refs"):
        validate_artifact_path(ref, "run record.provider_call_refs[]")
    return {**doc, "task_source": doc.get("task_source", "legacy")}


def validate_metrics_record(data: Any, *, expected_run_id: str | None = None) -> dict[str, Any]:
    doc = _mapping(data, "metrics record")
    _reject_unknown(doc, _METRICS_KEYS, "metrics record")
    _schema_version(doc, "metrics record")
    run_id = _required_str(doc, "run_id", "metrics record")
    if expected_run_id is not None and run_id != expected_run_id:
        raise SchemaError(
            f"metrics record.run_id {run_id!r} does not match run {expected_run_id!r}"
        )
    if "timestamps" in doc:
        timestamps = _mapping(doc.get("timestamps"), "metrics record.timestamps")
        _reject_unknown(timestamps, _TIMESTAMP_KEYS, "metrics record.timestamps")
        for key in _TIMESTAMP_KEYS:
            if key in timestamps:
                _optional_str_or_null(timestamps, key, "metrics record.timestamps")
    latency = _mapping(doc.get("latency_ms"), "metrics record.latency_ms")
    _reject_unknown(latency, _LATENCY_MS_KEYS, "metrics record.latency_ms")
    for key in ("end_to_end", "provider", "harness", "evaluator"):
        if key not in latency:
            raise SchemaError(f"metrics record.latency_ms.{key} is required")
    for key in _LATENCY_MS_KEYS - {"unavailable"}:
        if key in latency:
            _optional_non_negative_int_or_null(latency, key, "metrics record.latency_ms")
    if "unavailable" in latency:
        _str_list(latency["unavailable"], "metrics record.latency_ms.unavailable")
    usage = _mapping(doc.get("token_usage"), "metrics record.token_usage")
    _reject_unknown(usage, _TOKEN_USAGE_KEYS, "metrics record.token_usage")
    _closed(
        _required_str(usage, "accounting_source", "metrics record.token_usage"),
        TOKEN_ACCOUNTING_SOURCES,
        "metrics record.token_usage.accounting_source",
    )
    if "source_kind" in usage:
        _required_str(usage, "source_kind", "metrics record.token_usage")
    for key in _TOKEN_USAGE_KEYS - {"accounting_source", "source_kind"}:
        if key in usage:
            _optional_non_negative_int_or_null(usage, key, "metrics record.token_usage")
    provider_calls = _mapping(doc.get("provider_calls"), "metrics record.provider_calls")
    _reject_unknown(provider_calls, _PROVIDER_CALL_METRIC_KEYS, "metrics record.provider_calls")
    for key in provider_calls:
        _optional_non_negative_int_or_null(provider_calls, key, "metrics record.provider_calls")
    if "provider_call_records" in doc:
        records = _list(doc["provider_call_records"], "metrics record.provider_call_records")
        for i, item in enumerate(records):
            record = _mapping(item, f"metrics record.provider_call_records[{i}]")
            _reject_unknown(
                record,
                _PROVIDER_CALL_RECORD_KEYS,
                f"metrics record.provider_call_records[{i}]",
            )
            for key in (
                "call_id",
                "provider_id",
                "operation",
                "status",
                "started_at",
                "ended_at",
                "error_class",
            ):
                if key in record:
                    _optional_str_or_null(record, key, f"metrics record.provider_call_records[{i}]")
            if record.get("provider_id") is not None:
                _closed(
                    record["provider_id"],
                    PROVIDERS,
                    f"metrics record.provider_call_records[{i}].provider_id",
                )
            if record.get("status") is not None:
                _closed(
                    record["status"],
                    PROVIDER_CALL_STATUSES,
                    f"metrics record.provider_call_records[{i}].status",
                )
            for key in ("latency_ms", "retry_count", "source_ref_count"):
                if key in record:
                    _optional_non_negative_int_or_null(
                        record, key, f"metrics record.provider_call_records[{i}]"
                    )
    _required_non_negative_int(doc, "provider_result_chars", "metrics record")
    if "provider_result_context" in doc:
        context = _mapping(doc["provider_result_context"], "metrics record.provider_result_context")
        _reject_unknown(
            context, _PROVIDER_RESULT_CONTEXT_KEYS, "metrics record.provider_result_context"
        )
        for key in ("result_chars", "injected_chars"):
            _optional_non_negative_int_or_null(
                context, key, "metrics record.provider_result_context"
            )
        _optional_ratio_or_null(
            context, "compression_ratio", "metrics record.provider_result_context"
        )
        _required_str(context, "compression_mode", "metrics record.provider_result_context")
    source_counts = _mapping(doc.get("source_counts"), "metrics record.source_counts")
    _reject_unknown(source_counts, _SOURCE_COUNTS_KEYS, "metrics record.source_counts")
    for key in _SOURCE_COUNTS_KEYS:
        _required_non_negative_int(source_counts, key, "metrics record.source_counts")
    if "source_use" in doc:
        source_use = _mapping(doc["source_use"], "metrics record.source_use")
        _reject_unknown(source_use, _SOURCE_USE_KEYS, "metrics record.source_use")
        for key in ("normalized", "cited", "distinct_domains", "primary_source_count"):
            _required_non_negative_int(source_use, key, "metrics record.source_use")
        for key in ("reachable", "duplicate_or_mirror"):
            _optional_non_negative_int_or_null(source_use, key, "metrics record.source_use")
    if "failure_categories" in doc:
        _str_list(doc["failure_categories"], "metrics record.failure_categories")
    cost = _mapping(doc.get("cost"), "metrics record.cost")
    _reject_unknown(cost, _COST_KEYS, "metrics record.cost")
    _optional_str(cost, "currency", "metrics record.cost")
    _optional_non_negative_number_or_null(cost, "amount", "metrics record.cost")
    _required_str(cost, "source", "metrics record.cost")
    return doc


def validate_evaluation_record(data: Any, *, expected_run_id: str | None = None) -> dict[str, Any]:
    doc = _mapping(data, "evaluation record")
    _reject_unknown(doc, _EVALUATION_KEYS, "evaluation record")
    _schema_version(doc, "evaluation record")
    run_id = _required_str(doc, "run_id", "evaluation record")
    if expected_run_id is not None and run_id != expected_run_id:
        raise SchemaError(
            f"evaluation record.run_id {run_id!r} does not match run {expected_run_id!r}"
        )
    _closed(
        _required_str(doc, "outcome", "evaluation record"),
        EVALUATION_OUTCOMES,
        "evaluation record.outcome",
    )
    _required_str(doc, "task_id", "evaluation record")
    dimensions = _mapping(doc.get("dimensions"), "evaluation record.dimensions")
    _reject_unknown(dimensions, _EVALUATION_DIMENSION_KEYS, "evaluation record.dimensions")
    for key in _EVALUATION_DIMENSION_KEYS:
        _required_bool(dimensions, key, "evaluation record.dimensions")
    _str_list(doc.get("failure_reasons"), "evaluation record.failure_reasons")
    judge = _mapping(doc.get("judge"), "evaluation record.judge")
    _reject_unknown(judge, _EVALUATION_JUDGE_KEYS, "evaluation record.judge")
    _required_str(judge, "kind", "evaluation record.judge")
    for key in _EVALUATION_JUDGE_KEYS - {"kind"}:
        _optional_str(judge, key, "evaluation record.judge")
    for ref in _str_list(doc.get("evidence_refs"), "evaluation record.evidence_refs"):
        validate_artifact_path(ref, "evaluation record.evidence_refs[]")
    if "execution" in doc:
        execution = _mapping(doc["execution"], "evaluation record.execution")
        _closed(
            _required_str(execution, "outcome", "execution"), {"pass", "fail"}, "execution.outcome"
        )
        if execution["outcome"] != doc["outcome"]:
            raise SchemaError("execution outcome must match evaluation outcome")
        for key in ("escaped_defects", "visible_regressions"):
            if type(execution.get(key)) is not int or execution[key] < 0:
                raise SchemaError(f"execution.{key} must be a nonnegative integer")
        for key in ("visible", "hidden", "reruns", "flakes", "errors"):
            if not isinstance(execution.get(key), list):
                raise SchemaError(f"execution.{key} must be a list")
    return doc


def validate_normalized_source(data: Any) -> dict[str, Any]:
    doc = _mapping(data, "normalized source")
    _reject_unknown(doc, _SOURCE_KEYS, "normalized source")
    _schema_version(doc, "normalized source")
    _required_str(doc, "source_id", "normalized source")
    _closed(
        _required_str(doc, "provider_id", "normalized source"),
        PROVIDERS,
        "normalized source.provider_id",
    )
    url = _required_str(doc, "url", "normalized source")
    # A normalized source is a RECORD of what a provider returned, not an
    # instruction to fetch anything. Live providers do return http:// results
    # (Parallel and Firecrawl both do), and refusing to record them does not
    # make anything safer -- it makes the benchmark unable to describe reality,
    # and silently penalizes whichever provider returns them. Scheme safety is
    # enforced where it actually matters: the evaluator's reachability checker,
    # which pins handlers, refuses redirects, and filters link-local targets.
    # Everything other than http/https/fixture is still refused outright.
    if not (
        url.startswith("https://") or url.startswith("http://") or url.startswith("fixture://")
    ):
        raise SchemaError("normalized source.url must be http://, https:// or fixture://")
    _required_str(doc, "title", "normalized source")
    snippet = _required_str(doc, "snippet", "normalized source")
    if len(snippet) > 1200:
        raise SchemaError("normalized source.snippet must be <= 1200 characters")
    for key in ("published_at", "modified_at", "retrieved_at", "content_hash"):
        _optional_str(doc, key, "normalized source")
    if not isinstance(doc.get("content_length"), int) or doc["content_length"] < 0:
        raise SchemaError("normalized source.content_length must be a non-negative integer")
    redaction = _mapping(doc.get("redaction"), "normalized source.redaction")
    _reject_unknown(redaction, _SOURCE_REDACTION_KEYS, "normalized source.redaction")
    _required_str(redaction, "state", "normalized source.redaction")
    _required_bool(redaction, "raw_content_included", "normalized source.redaction")
    return doc


def validate_provider_call(data: Any, *, expected_run_id: str | None = None) -> dict[str, Any]:
    doc = _mapping(data, "provider call")
    _reject_unknown(doc, _PROVIDER_CALL_KEYS, "provider call")
    _schema_version(doc, "provider call")
    _required_str(doc, "call_id", "provider call")
    run_id = _required_str(doc, "run_id", "provider call")
    if expected_run_id is not None and run_id != expected_run_id:
        raise SchemaError(f"provider call.run_id {run_id!r} does not match run {expected_run_id!r}")
    _closed(
        _required_str(doc, "provider_id", "provider call"), PROVIDERS, "provider call.provider_id"
    )
    _closed(
        _required_str(doc, "status", "provider call"),
        PROVIDER_CALL_STATUSES,
        "provider call.status",
    )
    _required_str(doc, "operation", "provider call")
    _required_str(doc, "started_at", "provider call")
    _required_str(doc, "ended_at", "provider call")
    _mapping(doc.get("request"), "provider call.request")
    _mapping(doc.get("response"), "provider call.response")
    refs = _list(doc.get("normalized_source_refs"), "provider call.normalized_source_refs")
    for ref in refs:
        validate_artifact_path(ref, "provider call.normalized_source_refs[]")
    _required_non_negative_int(doc, "retry_count", "provider call")
    _optional_str(doc, "error_class", "provider call")
    return doc


def validate_harness_record(data: Any, *, expected_run_id: str | None = None) -> dict[str, Any]:
    doc = _mapping(data, "harness record")
    _reject_unknown(doc, _HARNESS_RECORD_KEYS, "harness record")
    _schema_version(doc, "harness record")
    run_id = _required_str(doc, "run_id", "harness record")
    if expected_run_id is not None and run_id != expected_run_id:
        raise SchemaError(
            f"harness record.run_id {run_id!r} does not match run {expected_run_id!r}"
        )
    _closed(
        _required_str(doc, "harness_id", "harness record"),
        HARNESSES,
        "harness record.harness_id",
    )
    _required_str(doc, "profile_id", "harness record")
    _required_str(doc, "resolved_model", "harness record")
    _closed(
        _required_str(doc, "tool_calling_mode", "harness record"),
        TOOL_CALLING_MODES,
        "harness record.tool_calling_mode",
    )
    _required_non_negative_int(doc, "context_window_tokens", "harness record")
    compression = _mapping(
        doc.get("provider_result_compression"), "harness record.provider_result_compression"
    )
    _reject_unknown(
        compression,
        _PROVIDER_RESULT_COMPRESSION_KEYS,
        "harness record.provider_result_compression",
    )
    _closed(
        _required_str(compression, "mode", "harness record.provider_result_compression"),
        PROVIDER_COMPRESSION_MODES,
        "harness record.provider_result_compression.mode",
    )
    _optional_non_negative_int_or_null(
        compression, "max_chars", "harness record.provider_result_compression"
    )
    _required_bool(
        compression, "preserves_source_refs", "harness record.provider_result_compression"
    )
    _closed(
        _required_str(doc, "token_accounting_source", "harness record"),
        TOKEN_ACCOUNTING_SOURCES,
        "harness record.token_accounting_source",
    )
    exposure = _mapping(
        doc.get("provider_adapter_exposure"), "harness record.provider_adapter_exposure"
    )
    _reject_unknown(
        exposure,
        _PROVIDER_ADAPTER_EXPOSURE_KEYS,
        "harness record.provider_adapter_exposure",
    )
    _required_bool(exposure, "exposed", "harness record.provider_adapter_exposure")
    _required_bool(exposure, "fixture_replay", "harness record.provider_adapter_exposure")
    _required_bool(exposure, "native_search_available", "harness record.provider_adapter_exposure")
    for provider in _str_list(
        exposure.get("provider_ids"), "harness record.provider_adapter_exposure.provider_ids"
    ):
        _closed(
            provider,
            PROVIDERS - {"fixture"},
            "harness record.provider_adapter_exposure.provider_ids[]",
        )
    _required_bool(doc, "fixture_mode", "harness record")
    return doc


def validate_evidence_bundle(data: Any, *, expected_run_id: str | None = None) -> dict[str, Any]:
    doc = _mapping(data, "evidence bundle")
    _reject_unknown(doc, _EVIDENCE_KEYS, "evidence bundle")
    _schema_version(doc, "evidence bundle")
    _required_str(doc, "bundle_id", "evidence bundle")
    run_id = _required_str(doc, "run_id", "evidence bundle")
    if expected_run_id is not None and run_id != expected_run_id:
        raise SchemaError(
            f"evidence bundle.run_id {run_id!r} does not match run {expected_run_id!r}"
        )
    redaction = _mapping(doc.get("redaction"), "evidence bundle.redaction")
    _reject_unknown(redaction, _EVIDENCE_REDACTION_KEYS, "evidence bundle.redaction")
    for key in _EVIDENCE_REDACTION_KEYS:
        if redaction.get(key) is not False:
            raise SchemaError(f"evidence bundle.redaction.{key} must be false")
    artifacts = _list(doc.get("artifacts"), "evidence bundle.artifacts")
    for i, artifact in enumerate(artifacts):
        table = _mapping(artifact, f"evidence bundle.artifacts[{i}]")
        _reject_unknown(table, _EVIDENCE_ARTIFACT_KEYS, f"evidence bundle.artifacts[{i}]")
        validate_artifact_path(
            _required_str(table, "path", f"evidence bundle.artifacts[{i}]"),
            f"evidence bundle.artifacts[{i}].path",
        )
        _required_str(table, "media_type", f"evidence bundle.artifacts[{i}]")
        size = table.get("size_bytes")
        if not isinstance(size, int) or size < 0 or size > 200_000:
            raise SchemaError(f"evidence bundle.artifacts[{i}].size_bytes must be 0..200000")
        _optional_str(table, "redaction", f"evidence bundle.artifacts[{i}]")
    records = _mapping(doc.get("records"), "evidence bundle.records")
    _reject_unknown(records, _EVIDENCE_RECORDS_KEYS, "evidence bundle.records")
    for key in ("run", "metrics", "evaluation"):
        validate_artifact_path(
            _required_str(records, key, "evidence bundle.records"),
            f"evidence bundle.records.{key}",
        )
    if "harness" in records:
        validate_artifact_path(
            _required_str(records, "harness", "evidence bundle.records"),
            "evidence bundle.records.harness",
        )
    for key in ("provider_calls", "normalized_sources"):
        for ref in _str_list(records.get(key), f"evidence bundle.records.{key}"):
            validate_artifact_path(ref, f"evidence bundle.records.{key}[]")
    return doc


def load_record(base: Path, ref: str) -> Any:
    return load_document(base / validate_artifact_path(ref, "record ref"))


def validate_fixture_run(run_dir: Path) -> FixtureValidation:
    run = validate_run_record(load_document(run_dir / "run.json"))
    run_id = run["run_id"]
    validate_metrics_record(load_record(run_dir, run["metrics_ref"]), expected_run_id=run_id)
    validate_evaluation_record(load_record(run_dir, run["evaluation_ref"]), expected_run_id=run_id)
    validate_evidence_bundle(
        load_record(run_dir, run["evidence_bundle_ref"]), expected_run_id=run_id
    )
    if "harness_ref" in run:
        validate_harness_record(load_record(run_dir, run["harness_ref"]), expected_run_id=run_id)
    for ref in run.get("provider_call_refs", []):
        validate_provider_call(load_record(run_dir, ref), expected_run_id=run_id)
    for source_path in sorted((run_dir / "sources").glob("*.json")):
        validate_normalized_source(load_document(source_path))
    return FixtureValidation(
        run_id=run_id,
        status=run["status"],
        metrics_ref=run["metrics_ref"],
        evaluation_ref=run["evaluation_ref"],
    )


def validate_fixture_tree(root: Path) -> list[FixtureValidation]:
    if not root.is_dir():
        raise SchemaError(f"fixture root must be a directory: {root}")
    results = []
    for child in sorted(
        path for path in root.iterdir() if path.is_dir() and (path / "run.json").is_file()
    ):
        results.append(validate_fixture_run(child))
    if not results:
        raise SchemaError(f"fixture root has no run directories: {root}")
    return results


def load_task_manifest(task_dir: Path) -> dict[str, Any]:
    return validate_task_manifest(load_document(task_dir / "task.yaml"))


def load_suite_manifest(suite_dir: Path) -> dict[str, Any]:
    return validate_suite_manifest(load_document(suite_dir / "suite.yaml"))


def validate_task_catalog(task_dirs: Iterable[Path]) -> list[str]:
    task_ids = []
    for task_dir in task_dirs:
        task = load_task_manifest(task_dir)
        if task["task_id"] != task_dir.name:
            raise SchemaError(f"{task_dir}/task.yaml task_id must match directory name")
        task_ids.append(task["task_id"])
    return task_ids
