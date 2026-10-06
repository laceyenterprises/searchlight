from __future__ import annotations

from sew.metrics import normalize_run_metrics, normalize_token_usage, report_ready_row


RUN = {
    "schema_version": 1,
    "run_id": "run-metrics-1",
    "started_at": "2026-09-16T18:12:00Z",
    "ended_at": "2026-09-16T18:12:10Z",
}

PROVIDER_CALLS = [
    {
        "schema_version": 1,
        "call_id": "call-1",
        "run_id": "run-metrics-1",
        "provider_id": "exa",
        "operation": "search",
        "status": "ok",
        "started_at": "2026-09-16T18:12:02Z",
        "ended_at": "2026-09-16T18:12:03Z",
        "normalized_source_refs": ["sources/a.json"],
        "retry_count": 1,
    },
    {
        "schema_version": 1,
        "call_id": "call-2",
        "run_id": "run-metrics-1",
        "provider_id": "exa",
        "operation": "fetch",
        "status": "timeout",
        "started_at": "2026-09-16T18:12:04Z",
        "ended_at": "2026-09-16T18:12:07Z",
        "normalized_source_refs": ["sources/b.json"],
        "retry_count": 2,
        "error_class": "provider_timeout",
    },
]

SOURCES = [
    {
        "source_id": "src-a",
        "url": "https://docs.example/current",
        "snippet": "A" * 20,
        "retrieved_at": "2026-09-16T18:12:03Z",
    },
    {
        "source_id": "src-b",
        "url": "https://news.example/item",
        "snippet": "B" * 30,
        "retrieved_at": "2026-09-16T18:12:07Z",
    },
]

TRANSCRIPT_EVENTS = [
    {"event": "harness_ready", "timestamp": "2026-09-16T18:12:01Z"},
    {"event": "prompt_sent", "timestamp": "2026-09-16T18:12:01Z"},
    {"event": "first_output_token", "timestamp": "2026-09-16T18:12:08Z"},
    {"event": "final_answer", "timestamp": "2026-09-16T18:12:09Z"},
]


def test_token_source_precedence_prefers_ledger_then_harness_rows() -> None:
    usage = normalize_token_usage(
        session_ledger_usage={
            "token_usage_input": 10,
            "token_usage_output": 5,
            "token_usage_cache_read": 2,
            "token_usage_reasoning": 3,
        },
        harness_usage_rows=[{"input": 99, "output": 99}],
    )

    assert usage == {
        "accounting_source": "measured",
        "source_kind": "session_ledger",
        "input": 10,
        "cached_input": 2,
        "output": 5,
        "reasoning": 3,
        "total_billable": 15,
    }


def test_claude_cache_write_metadata_survives_harness_normalization() -> None:
    usage = normalize_token_usage(
        harness_usage_rows=[
            {
                "input": 112,
                "cached_input": 900,
                "output": 40,
                "reasoning": 0,
                "total_billable": 1052,
                "cache_write": 100,
                "cache_write_5m": 40,
                "cache_write_1h": 60,
            }
        ]
    )
    assert usage["total_billable"] == 1052
    assert (usage["cache_write"], usage["cache_write_5m"], usage["cache_write_1h"]) == (100, 40, 60)


def test_estimated_and_unknown_token_classifications_are_visible() -> None:
    estimated = normalize_token_usage(tokenizer_estimate={"input": 100, "output": 25})
    unknown = normalize_token_usage()

    assert estimated["accounting_source"] == "estimated"
    assert estimated["source_kind"] == "tokenizer_estimate"
    assert unknown == {
        "accounting_source": "unknown",
        "source_kind": "unknown",
        "input": None,
        "cached_input": None,
        "output": None,
        "reasoning": None,
        "total_billable": None,
    }


def test_harness_usage_rows_preserve_unmeasured_token_dimensions() -> None:
    usage = normalize_token_usage(harness_usage_rows=[{"input": 10, "output": 5}])

    assert usage == {
        "accounting_source": "measured",
        "source_kind": "harness_usage_rows",
        "input": 10,
        "cached_input": None,
        "output": 5,
        "reasoning": None,
        "total_billable": 15,
    }


def test_unknown_tokens_remain_unknown_in_report_ready_row() -> None:
    metrics = normalize_run_metrics(run_record=RUN, tokenizer_estimate=None)
    row = report_ready_row(metrics)

    assert metrics["token_usage"]["accounting_source"] == "unknown"
    assert row["input_tokens"] is None
    assert row["total_billable_tokens"] is None


def test_latency_decomposition_reports_missing_submetrics_as_unavailable() -> None:
    metrics = normalize_run_metrics(
        run_record=RUN,
        provider_calls=PROVIDER_CALLS,
        sources=SOURCES,
        evaluation_record={
            "evidence_refs": ["sources/a.json"],
            "started_at": "2026-09-16T18:12:09Z",
            "ended_at": "2026-09-16T18:12:10Z",
        },
        transcript=TRANSCRIPT_EVENTS,
        timeout_seconds=8,
    )

    assert metrics["timestamps"]["first_source_at"] == "2026-09-16T18:12:03Z"
    assert metrics["latency_ms"]["total_wall"] == 10_000
    assert metrics["latency_ms"]["harness_boot"] == 1_000
    assert metrics["latency_ms"]["provider_total"] == 4_000
    assert metrics["latency_ms"]["post_provider_synthesis"] == 2_000
    assert metrics["latency_ms"]["evaluator"] == 1_000
    assert metrics["latency_ms"]["timeout_debt"] == 2_000
    assert metrics["latency_ms"]["unavailable"] == []


def test_missing_latency_timestamps_are_unknown_not_zero() -> None:
    metrics = normalize_run_metrics(run_record=RUN)

    assert metrics["latency_ms"]["harness_boot"] is None
    assert metrics["latency_ms"]["post_provider_synthesis"] is None
    assert "harness_boot" in metrics["latency_ms"]["unavailable"]


def test_provider_call_metrics_aggregate_without_losing_audit_records() -> None:
    metrics = normalize_run_metrics(run_record=RUN, provider_calls=PROVIDER_CALLS)

    assert metrics["provider_calls"]["total"] == 2
    assert metrics["provider_calls"]["ok"] == 1
    assert metrics["provider_calls"]["timeout"] == 1
    assert metrics["provider_calls"]["retry_count"] == 3
    assert [record["call_id"] for record in metrics["provider_call_records"]] == [
        "call-1",
        "call-2",
    ]
    assert metrics["failure_categories"] == ["provider_timeout"]
