"""SEW metrics normalization from run, harness, provider, and ledger records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from .schema import PROVIDER_CALL_STATUSES, SchemaError, validate_metrics_record

TIMESTAMP_KEYS = (
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
)

TOKEN_FIELDS = ("input", "cached_input", "output", "reasoning", "total_billable")
TOKEN_SOURCE_ORDER = (
    ("session_ledger", "measured"),
    ("harness_usage_rows", "measured"),
    ("harness_transcript_metadata", "measured"),
    ("provider_model_server_usage", "measured"),
    ("tokenizer_estimate", "estimated"),
)


@dataclass(frozen=True)
class TokenCandidate:
    source_kind: str
    accounting_source: str
    usage: Mapping[str, Any] | None


def normalize_run_metrics(
    *,
    run_record: Mapping[str, Any],
    provider_calls: Iterable[Mapping[str, Any]] = (),
    sources: Iterable[Mapping[str, Any]] = (),
    evaluation_record: Mapping[str, Any] | None = None,
    harness_record: Mapping[str, Any] | None = None,
    session_ledger_usage: Mapping[str, Any] | None = None,
    harness_usage_rows: Iterable[Mapping[str, Any]] = (),
    transcript: Any = None,
    provider_model_usage: Mapping[str, Any] | None = None,
    tokenizer_estimate: Mapping[str, Any] | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Build the report-ready SEW metrics record for one run.

    Missing timestamps and token fields remain ``None``. The normalizer never
    coerces unknown usage into zero, because zero is a measurement.
    """

    calls = [dict(call) for call in provider_calls]
    normalized_sources = [dict(source) for source in sources]
    run_id = _required_str(run_record, "run_id", "run record")
    timestamps = _timestamps(run_record, calls, normalized_sources, evaluation_record, transcript)
    latency = _latency_breakdown(timestamps, calls, timeout_seconds=timeout_seconds)
    token_usage = normalize_token_usage(
        session_ledger_usage=session_ledger_usage,
        harness_usage_rows=harness_usage_rows,
        transcript=transcript,
        provider_model_usage=provider_model_usage,
        tokenizer_estimate=tokenizer_estimate,
    )
    provider_summary, provider_records = _provider_call_metrics(calls)
    source_use = _source_use_metrics(normalized_sources, evaluation_record)
    provider_context = _provider_context_metrics(normalized_sources, harness_record)
    failure_categories = _failure_categories(run_record, evaluation_record, calls)
    metrics = {
        "schema_version": 1,
        "run_id": run_id,
        "timestamps": timestamps,
        "latency_ms": latency,
        "token_usage": token_usage,
        "provider_calls": provider_summary,
        "provider_call_records": provider_records,
        "provider_result_context": provider_context,
        "provider_result_chars": provider_context["injected_chars"],
        "source_counts": {
            "normalized": source_use["normalized"],
            "cited": source_use["cited"],
        },
        "source_use": source_use,
        "failure_categories": failure_categories,
        "cost": {
            "currency": "USD",
            "amount": None,
            "source": token_usage["accounting_source"],
        },
    }
    return validate_metrics_record(metrics, expected_run_id=run_id)


def normalize_token_usage(
    *,
    session_ledger_usage: Mapping[str, Any] | None = None,
    harness_usage_rows: Iterable[Mapping[str, Any]] = (),
    transcript: Any = None,
    provider_model_usage: Mapping[str, Any] | None = None,
    tokenizer_estimate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rows = [dict(row) for row in harness_usage_rows]
    usage_by_source = {
        "session_ledger": session_ledger_usage,
        "harness_usage_rows": _sum_usage_rows(rows),
        "harness_transcript_metadata": _usage_from_transcript(transcript),
        "provider_model_server_usage": provider_model_usage,
        "tokenizer_estimate": tokenizer_estimate,
    }
    candidates = tuple(
        TokenCandidate(source_kind, accounting_source, usage_by_source[source_kind])
        for source_kind, accounting_source in TOKEN_SOURCE_ORDER
    )
    for candidate in candidates:
        normalized = _normalize_usage_mapping(candidate.usage)
        if normalized is None:
            continue
        return {
            "accounting_source": candidate.accounting_source,
            "source_kind": candidate.source_kind,
            **normalized,
        }
    return {
        "accounting_source": "unknown",
        "source_kind": "unknown",
        "input": None,
        "cached_input": None,
        "output": None,
        "reasoning": None,
        "total_billable": None,
    }


def report_ready_row(metrics: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten selected metrics for early report aggregation.

    Unknown token and latency values remain ``None`` so report code can count
    unknown rates instead of summing fabricated zeros.
    """

    usage = _mapping(metrics.get("token_usage"), "metrics.token_usage")
    latency = _mapping(metrics.get("latency_ms"), "metrics.latency_ms")
    provider = _mapping(metrics.get("provider_calls"), "metrics.provider_calls")
    source_use = _mapping(metrics.get("source_use", {}), "metrics.source_use")
    return {
        "run_id": metrics.get("run_id"),
        "token_accounting_source": usage.get("accounting_source", "unknown"),
        "token_source_kind": usage.get("source_kind", "unknown"),
        "input_tokens": usage.get("input"),
        "cached_input_tokens": usage.get("cached_input"),
        "output_tokens": usage.get("output"),
        "reasoning_tokens": usage.get("reasoning"),
        "total_billable_tokens": usage.get("total_billable"),
        "total_wall_latency_ms": latency.get("total_wall"),
        "harness_boot_latency_ms": latency.get("harness_boot"),
        "provider_latency_ms": latency.get("provider_total"),
        "post_provider_synthesis_latency_ms": latency.get("post_provider_synthesis"),
        "evaluator_latency_ms": latency.get("evaluator"),
        "retry_count": provider.get("retry_count"),
        "timeout_debt_ms": latency.get("timeout_debt"),
        "provider_result_chars": metrics.get("provider_result_chars"),
        "source_count": source_use.get("normalized"),
        "failure_categories": list(metrics.get("failure_categories") or []),
    }


def _timestamps(
    run_record: Mapping[str, Any],
    provider_calls: list[Mapping[str, Any]],
    sources: list[Mapping[str, Any]],
    evaluation_record: Mapping[str, Any] | None,
    transcript: Any,
) -> dict[str, str | None]:
    values = {key: _optional_str(run_record, key) for key in TIMESTAMP_KEYS}
    values["started_at"] = values["started_at"] or _optional_str(run_record, "started_at")
    values["completed_at"] = values["completed_at"] or _optional_str(run_record, "completed_at")
    values["completed_at"] = values["completed_at"] or _optional_str(run_record, "ended_at")
    if provider_calls:
        starts = [
            call.get("started_at")
            for call in provider_calls
            if isinstance(call.get("started_at"), str)
        ]
        ends = [
            call.get("ended_at") for call in provider_calls if isinstance(call.get("ended_at"), str)
        ]
        values["provider_call_first_started_at"] = values["provider_call_first_started_at"] or min(
            starts, default=None
        )
        values["provider_call_last_ended_at"] = values["provider_call_last_ended_at"] or max(
            ends, default=None
        )
    source_times = [
        source.get("retrieved_at")
        for source in sources
        if isinstance(source.get("retrieved_at"), str)
    ]
    values["first_source_at"] = values["first_source_at"] or min(source_times, default=None)
    if evaluation_record:
        values["evaluator_started_at"] = values["evaluator_started_at"] or _optional_str(
            evaluation_record, "started_at"
        )
        values["evaluator_ended_at"] = values["evaluator_ended_at"] or _optional_str(
            evaluation_record, "ended_at"
        )
    transcript_times = _event_timestamps(transcript)
    for key in (
        "harness_ready_at",
        "prompt_sent_at",
        "first_output_token_at",
        "final_answer_at",
    ):
        values[key] = values[key] or transcript_times.get(key)
    return values


def _latency_breakdown(
    timestamps: Mapping[str, str | None],
    provider_calls: list[Mapping[str, Any]],
    *,
    timeout_seconds: int | None,
) -> dict[str, Any]:
    total_wall = _delta_ms(timestamps.get("started_at"), timestamps.get("completed_at"))
    provider_total = 0 if not provider_calls else None
    if provider_calls:
        durations = [
            _delta_ms(call.get("started_at"), call.get("ended_at")) for call in provider_calls
        ]
        provider_total = sum(duration for duration in durations if duration is not None)
        if len([duration for duration in durations if duration is not None]) != len(provider_calls):
            provider_total = None
    timeout_debt = None
    if timeout_seconds is not None and total_wall is not None:
        timeout_debt = max(0, total_wall - timeout_seconds * 1000)
    result = {
        "end_to_end": total_wall,
        "provider": provider_total,
        "harness": _delta_ms(timestamps.get("started_at"), timestamps.get("final_answer_at")),
        "evaluator": _delta_ms(
            timestamps.get("evaluator_started_at"), timestamps.get("evaluator_ended_at")
        ),
        "total_wall": total_wall,
        "harness_boot": _delta_ms(timestamps.get("started_at"), timestamps.get("harness_ready_at")),
        "provider_total": provider_total,
        "post_provider_synthesis": _delta_ms(
            timestamps.get("provider_call_last_ended_at"), timestamps.get("final_answer_at")
        ),
        "timeout_debt": timeout_debt,
    }
    result["unavailable"] = sorted(
        key
        for key in ("harness_boot", "post_provider_synthesis", "evaluator", "timeout_debt")
        if result[key] is None
    )
    return result


def _provider_call_metrics(
    provider_calls: list[Mapping[str, Any]],
) -> tuple[dict[str, int | None], list[dict[str, Any]]]:
    counts: dict[str, int | None] = {
        "total": len(provider_calls),
        "retry_count": 0,
        "latency_ms_total": 0,
    }
    for status in PROVIDER_CALL_STATUSES:
        counts[status] = 0
    records = []
    latency_unknown = False
    for call in provider_calls:
        status = str(call.get("status") or "failed")
        if status in PROVIDER_CALL_STATUSES:
            counts[status] = int(counts[status] or 0) + 1
        retry_count = _nonnegative_int(call.get("retry_count")) or 0
        counts["retry_count"] = int(counts["retry_count"] or 0) + retry_count
        latency_ms = _delta_ms(call.get("started_at"), call.get("ended_at"))
        if latency_ms is None:
            latency_unknown = True
        else:
            counts["latency_ms_total"] = int(counts["latency_ms_total"] or 0) + latency_ms
        records.append(
            {
                "call_id": call.get("call_id"),
                "provider_id": call.get("provider_id"),
                "operation": call.get("operation"),
                "status": call.get("status"),
                "started_at": call.get("started_at"),
                "ended_at": call.get("ended_at"),
                "latency_ms": latency_ms,
                "retry_count": retry_count,
                "error_class": call.get("error_class"),
                "source_ref_count": len(call.get("normalized_source_refs") or []),
            }
        )
    if latency_unknown:
        counts["latency_ms_total"] = None
    return counts, records


def _source_use_metrics(
    sources: list[Mapping[str, Any]], evaluation_record: Mapping[str, Any] | None
) -> dict[str, Any]:
    cited_refs = set(evaluation_record.get("evidence_refs") or []) if evaluation_record else set()
    domains = {
        urlparse(str(source.get("url") or "")).netloc.lower()
        for source in sources
        if source.get("url")
    }
    domains.discard("")
    return {
        "normalized": len(sources),
        "cited": len(cited_refs),
        "distinct_domains": len(domains),
        "primary_source_count": sum(
            1 for source in sources if source.get("primary_source") is True
        ),
        "reachable": None,
        "duplicate_or_mirror": None,
    }


def _provider_context_metrics(
    sources: list[Mapping[str, Any]], harness_record: Mapping[str, Any] | None
) -> dict[str, Any]:
    result_chars = sum(len(str(source.get("snippet") or "")) for source in sources)
    compression = {}
    if harness_record:
        raw_compression = harness_record.get("provider_result_compression")
        if isinstance(raw_compression, Mapping):
            compression = dict(raw_compression)
    injected_chars = _nonnegative_int(compression.get("injected_chars")) if compression else None
    original_chars = _nonnegative_int(compression.get("original_chars")) if compression else None
    injected_chars = result_chars if injected_chars is None else injected_chars
    ratio = None
    if original_chars and injected_chars is not None:
        ratio = injected_chars / original_chars
    return {
        "result_chars": result_chars,
        "injected_chars": injected_chars,
        "compression_ratio": ratio,
        "compression_mode": compression.get("mode", "unknown"),
    }


def _failure_categories(
    run_record: Mapping[str, Any],
    evaluation_record: Mapping[str, Any] | None,
    provider_calls: list[Mapping[str, Any]],
) -> list[str]:
    categories = []
    if run_record.get("failure_category"):
        categories.append(str(run_record["failure_category"]))
    if evaluation_record:
        categories.extend(str(reason) for reason in evaluation_record.get("failure_reasons") or [])
    categories.extend(
        str(call["error_class"]) for call in provider_calls if call.get("error_class")
    )
    return sorted(set(categories))


def _normalize_usage_mapping(usage: Mapping[str, Any] | None) -> dict[str, int | None] | None:
    if not usage:
        return None
    normalized = {
        "input": _first_int(usage, "input", "input_tokens", "prompt_tokens", "token_usage_input"),
        "cached_input": _first_int(
            usage,
            "cached_input",
            "cached_input_tokens",
            "cache_read_input_tokens",
            "token_usage_cache_read",
        ),
        "output": _first_int(
            usage, "output", "output_tokens", "completion_tokens", "token_usage_output"
        ),
        "reasoning": _first_int(usage, "reasoning", "reasoning_tokens", "token_usage_reasoning"),
        "total_billable": _first_int(
            usage, "total_billable", "total_tokens", "token_usage_running_total"
        ),
    }
    if all(value is None for value in normalized.values()):
        return None
    if normalized["total_billable"] is None and all(
        normalized[key] is not None for key in ("input", "output")
    ):
        normalized["total_billable"] = sum(int(normalized[key] or 0) for key in ("input", "output"))
    # These are subsets of input, not additional disjoint budget buckets.
    for key in ("cache_write", "cache_write_5m", "cache_write_1h"):
        if key in usage:
            normalized[key] = _first_int(usage, key)
    return normalized


def _sum_usage_rows(rows: list[Mapping[str, Any]]) -> dict[str, int] | None:
    if not rows:
        return None
    totals: dict[str, int] = {}
    seen = False
    normalized_rows = []
    for row in rows:
        normalized = _normalize_usage_mapping(row)
        if normalized is None:
            continue
        normalized_rows.append(normalized)
        seen = True
        for key in TOKEN_FIELDS:
            value = normalized.get(key)
            if value is not None:
                totals[key] = totals.get(key, 0) + value
    for key in ("cache_write", "cache_write_5m", "cache_write_1h"):
        if normalized_rows and all(isinstance(row.get(key), int) for row in normalized_rows):
            totals[key] = sum(int(row[key]) for row in normalized_rows)
    return totals if seen else None


def _usage_from_transcript(transcript: Any) -> Mapping[str, Any] | None:
    if isinstance(transcript, Mapping):
        for key in ("usage", "token_usage", "metadata"):
            value = transcript.get(key)
            if isinstance(value, Mapping):
                normalized = _usage_from_transcript(value)
                if normalized:
                    return normalized
        return transcript if _normalize_usage_mapping(transcript) else None
    if isinstance(transcript, list | tuple):
        for event in reversed(transcript):
            if isinstance(event, Mapping):
                for key in ("usage", "token_usage"):
                    value = event.get(key)
                    if isinstance(value, Mapping) and _normalize_usage_mapping(value):
                        return value
    return None


def _event_timestamps(transcript: Any) -> dict[str, str]:
    values: dict[str, str] = {}
    if not isinstance(transcript, list | tuple):
        return values
    event_map = {
        "harness_ready": "harness_ready_at",
        "prompt_sent": "prompt_sent_at",
        "first_output_token": "first_output_token_at",
        "final_answer": "final_answer_at",
    }
    for event in transcript:
        if not isinstance(event, Mapping):
            continue
        name = event.get("event") or event.get("type")
        ts = event.get("timestamp") or event.get("at")
        if isinstance(name, str) and isinstance(ts, str) and name in event_map:
            values.setdefault(event_map[name], ts)
    return values


def _delta_ms(start: Any, end: Any) -> int | None:
    start_dt = _parse_time(start)
    end_dt = _parse_time(end)
    if start_dt is None or end_dt is None:
        return None
    delta = int((end_dt - start_dt).total_seconds() * 1000)
    return delta if delta >= 0 else None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _first_int(data: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key in data:
            value = _nonnegative_int(data[key])
            if value is not None:
                return value
    usage = data.get("usage")
    if isinstance(usage, Mapping):
        return _first_int(usage, *keys)
    details = data.get("prompt_tokens_details")
    if isinstance(details, Mapping) and "cached_input" in keys:
        return _first_int(details, "cached_tokens", "cache_read_input_tokens")
    return None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    return None


def _required_str(data: Mapping[str, Any], key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise SchemaError(f"{where}.{key} is required")
    return value


def _optional_str(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SchemaError(f"{where} must be an object")
    return value
