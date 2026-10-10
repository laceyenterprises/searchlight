"""WSB-09 comparative bakeoff report over a completed suite run.

The operator's question is a delta against a baseline: does giving a harness a
search provider's tools make it finish more real work, for fewer tokens and
fewer dollars, than giving it no search or its own native search? This module
answers that from the run's own records, and says so wherever the records
cannot answer it.

Four rules carry the module.

**A rate counts only runs the arm is answerable for.** A run is *attempted*
when the arm itself decided the outcome: it delivered (``succeeded``) or it
ran out of road (``failed``, ``timeout``, ``budget_exhausted``). Contaminated,
non-comparable, and infrastructure runs (``provider_unavailable``,
``harness_boot_failed``, ``cancelled``) leave the denominator and are counted
as marks instead. A rate-limited call is not a quality result and a boot flake
is not a wrong answer. A delivered run with no pass/fail verdict is
*ungraded*: it stays attempted, and the completion rate of any cell holding one
is withheld, because counting only the graded failures would manufacture a low
rate out of missing grades.

**Tokens and dollars are measured or absent.** Tokens per task is the mean of
*measured* usage over attempted runs. A run whose usage is unknown or estimated
is left out of every token and cost figure and counted in a visible warning;
it is never a zero. Cost comes from the WSB-04 cost model, and a run whose
search calls were made but never priced has an unknown cost, not a free one.

**A delta names its control and its denominator.** Deltas compare an arm with
a declared control arm on the same harness, model profile, and task class.
Success change is in percentage points with a Newcombe interval; token change
is a ratio of measured tokens per attempted task, and both sides' run counts
are printed beside it. A delta against an absent, empty, or under-sampled cell
is marked and left blank rather than dropped.

**An OSS model names its rate.** A ``litellm/<route>`` model is priced from the
versioned OSS catalog (``config/oss-models.yaml`` under ``module_base``, or the
installed catalog for source runs without one). Published bundles snapshot it;
the same catalog supplies token prices and rate labels. Its cells carry the route
and the catalog's rate basis, a dated list rate or a self-hosted zero, so a
cheap cell is never mistaken for a measured bill. Reports without OSS models
render exactly as before.

**Nothing is a verdict.** The report is broken down by harness, arm, and task
class. The per-arm headline is an explicitly labelled roll-up of the arm's own
publishable task-class cells, and no row ranks one arm above another.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from .mcp_meter import meter_observed
from .oss import load_catalog

from . import report as sew_report
from .arms import PROVIDER_SERVER_NAMES, _tool_call_entries
from .catalog import module_root
from .cost_model import (
    PriceTable,
    PriceTableError,
    cell_cost,
    load_price_table,
    model_cost,
    summarize_arm_costs,
    vendor_call,
    vendor_call_from_record,
)
from .production_catalog import load_production_catalog
from .pricing_coverage import pricing_coverage, render_pricing_coverage
from .report import (
    ReportArtifacts,
    ReportError,
    RunRow,
    _ci,
    _latency_values,
    _load_index,
    _load_rows,
    _load_state,
    _load_task_classes,
    _md_cell,
    _pct,
    _percentile,
    _portable_link,
    _signed_pct,
    _table,
    _total_tokens,
    _wilson_ci,
    under_sampled,
)
from .schema import SchemaError, load_record

REPORT_SCHEMA_VERSION = 2
JSON_REPORT_NAME = "bakeoff-report.json"
MARKDOWN_REPORT_NAME = "bakeoff-report.md"
ALL_TASK_CLASSES = "all"
SPAWN_METADATA_REF = "artifacts/spawn-metadata.json"
TRANSCRIPT_REF = "artifacts/transcript.json"
EVIDENCE_LINKS_SHOWN = 3


@dataclass(frozen=True)
class Control:
    """A declared control arm, matched on harness, model profile, and task class."""

    name: str
    provider_id: str


# The two baselines the operator asked for (SPEC section 10 arm naming).
DEFAULT_CONTROLS = (Control("no_search", "no-search"), Control("native", "native"))

TASK_FAILURE_STATUSES = frozenset({"failed", "timeout", "budget_exhausted"})
NON_COMPARABLE_STATUSES = frozenset({"not_applicable", "unsupported"})
INFRASTRUCTURE_STATUSES = frozenset({"provider_unavailable", "harness_boot_failed", "cancelled"})

SUCCESS = "success"
FAILURE = "failure"
UNGRADED = "ungraded"
CONTAMINATED = "contaminated"
NON_COMPARABLE = "non_comparable"
UNCLASSIFIED = "unclassified"
ATTEMPTED = frozenset({SUCCESS, FAILURE, UNGRADED})

UNKNOWN_TOKENS = "unknown_tokens"
ESTIMATED_TOKENS = "estimated_tokens"
UNKNOWN_COST = "unknown_cost"
UNDER_SAMPLED = "under_sampled"
TELEMETRY_INCOMPLETE = "telemetry_incomplete"
NO_PUBLISHABLE_CLASS = "no_publishable_task_class"

# What each mark means, printed with the report so a marked cell explains itself.
MARK_LEGEND = {
    NON_COMPARABLE: "run not applicable to this arm (no capability, not allowed in this mode)",
    UNDER_SAMPLED: "fewer attempted runs than the minimum; kept out of headline and deltas",
    CONTAMINATED: "transcript shows a tool call outside the arm; failed, never scored",
    UNKNOWN_TOKENS: "usage unknown; excluded from token and cost figures, never zero",
    ESTIMATED_TOKENS: "usage estimated, not measured; excluded from token and cost figures",
    UNKNOWN_COST: "tokens measured but spend could not be priced; excluded from cost figures",
    UNGRADED: "delivered without a pass/fail verdict; the cell's completion rate is withheld",
    "provider_unavailable": "provider refused or rate-limited; excluded from rates",
    "harness_boot_failed": "harness never booted; excluded from rates",
    "cancelled": "cancelled by the operator; excluded from rates",
    UNCLASSIFIED: "terminal status the report does not recognise; excluded from rates",
    TELEMETRY_INCOMPLETE: "a run record could not be read; see warnings",
}

DEFINITIONS = {
    "attempted": (
        "runs whose outcome the arm decided: succeeded, failed, timeout, budget_exhausted"
    ),
    "completion_rate": (
        "passed runs / attempted runs, with a 95% Wilson interval; withheld when any "
        "attempted run is ungraded"
    ),
    "tokens_per_task": (
        "mean measured total_billable tokens over attempted runs with measured usage; "
        "measured_n of attempted_n is printed beside it"
    ),
    "usd_per_success": (
        "WSB-04 spend over runs with a known cost / passed runs among those same runs; "
        "undefined with zero successes"
    ),
    "success_delta": (
        "arm completion rate minus the control's, in percentage points, with a 95% "
        "Newcombe (Wilson score) interval"
    ),
    "token_ratio": (
        "arm tokens_per_task / control tokens_per_task, same harness, model profile, and task class"
    ),
    "graded_success_rate": (
        "passed runs / graded attempted runs; always published, beside the ungraded count, "
        "even when the completion rate is withheld"
    ),
    "tokens_per_success": (
        "measured total_billable tokens over graded attempted runs / passed runs among "
        "those same runs; undefined with zero successes"
    ),
    "token_mix": (
        "mean measured tokens per attempted task by disjoint bucket: fresh input (cache "
        "writes included) / cache reads / output (reasoning included); buckets sum "
        "to the mean total_billable tokens over the mix_n runs"
    ),
    "headline": (
        "per-arm roll-up over the arm's task classes that are not under-sampled; not a ranking"
    ),
}


@dataclass(frozen=True)
class BakeoffRun:
    row: RunRow
    arm_key: tuple[str, str, str]
    disposition: str
    token_status: str
    tokens: int | None
    model_id: str | None
    model_id_source: str | None
    search_calls: int
    cost: Mapping[str, Any] | None
    cost_exclusions: tuple[str, ...]
    evidence: Mapping[str, Any]
    token_parts: Mapping[str, int] | None = None


# Disjoint usage buckets (live_harness): input + cached_input + output + reasoning
# = total_billable. Cache writes stay inside input.
TOKEN_PARTS = ("input", "cached_input", "output", "reasoning")
# An OSS model is named by its LiteLLM route (sew.oss).
OSS_MODEL_PREFIX = "litellm/"


def generate_bakeoff_report(
    run_root: Path,
    *,
    module_base: Path | None = None,
    generated_at: str | None = None,
    output_dir: Path | None = None,
    controls: Sequence[Control] = DEFAULT_CONTROLS,
    price_table_path: Path | None = None,
) -> ReportArtifacts:
    """Write ``bakeoff-report.json`` and ``bakeoff-report.md`` for a suite run root."""

    destination = output_dir or run_root / "reports"
    try:
        table = load_price_table(price_table_path)
    except (OSError, PriceTableError, yaml.YAMLError) as exc:
        raise ReportError(f"cannot load price table: {exc}") from exc
    report = build_bakeoff_report(
        run_root,
        module_base=module_base,
        generated_at=generated_at,
        output_dir=destination,
        controls=controls,
        price_table=table,
    )
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / JSON_REPORT_NAME
    markdown_path = destination / MARKDOWN_REPORT_NAME
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_bakeoff_markdown(report), encoding="utf-8")
    return ReportArtifacts(json_path=json_path, markdown_path=markdown_path)


def build_bakeoff_report(
    run_root: Path,
    *,
    module_base: Path | None = None,
    generated_at: str | None = None,
    output_dir: Path | None = None,
    controls: Sequence[Control] = DEFAULT_CONTROLS,
    price_table: PriceTable | None = None,
) -> dict[str, Any]:
    base = module_base or module_root()
    report_dir = output_dir or run_root / "reports"
    declared = _validate_controls(controls)
    table = price_table if price_table is not None else load_price_table()
    if table.oss_models is None:
        catalog_path = base / "config" / "oss-models.yaml"
        table = replace(table, oss_models=load_catalog(catalog_path if catalog_path.is_file() else None))
    state = _load_state(run_root)
    index = _load_index(run_root, state)
    rows = _load_rows(index, _task_classes(base, state, index), link_base=report_dir)
    runs = [_bakeoff_run(row, table, report_dir) for row in rows]

    grouped: dict[tuple[tuple[str, str, str], str], list[BakeoffRun]] = defaultdict(list)
    for run in runs:
        grouped[(run.arm_key, run.row.task_class)].append(run)
    cells = {
        key: _aggregate(items, arm_key=key[0], task_class=key[1], oss_models=table.oss_models)
        for key, items in grouped.items()
    }
    arm_keys = _sorted_arm_keys({key[0] for key in cells}, declared)
    headline = [_headline(arm_key, cells, grouped, table.oss_models) for arm_key in arm_keys]
    by_class = [
        cells[(arm_key, task_class)]
        for task_class in sorted({key[1] for key in cells})
        for arm_key in arm_keys
        if (arm_key, task_class) in cells
    ]
    deltas = _deltas(arm_keys, cells, grouped, declared, table.oss_models)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report": "wsb-bakeoff",
        "suite_id": state.get("suite_id"),
        "suite_version": state.get("suite_version"),
        "suite_run_id": run_root.name,
        "mode": state.get("mode", "fixture"),
        "seed": state.get("seed"),
        "generated_at": generated_at or datetime.now(UTC).replace(microsecond=0).isoformat(),
        "min_repetitions": sew_report.MIN_COMPARABLE_REPETITIONS,
        "controls": [
            {
                "name": control.name,
                "provider_id": control.provider_id,
                "matched_on": ["harness_id", "model_profile", "task_class"],
            }
            for control in declared
        ],
        "definitions": dict(DEFINITIONS),
        "mark_legend": dict(MARK_LEGEND),
        "run_counts": {
            "runs": len(runs),
            "metering": {
                "observed_n": sum(
                    meter_observed(r.row.run_dir, r.row.provider_id, r.row.run_id) for r in runs
                ),
                "cells_n": len(runs),
            },
            "by_disposition": dict(sorted(Counter(run.disposition for run in runs).items())),
        },
        "headline": headline,
        "by_task_class": by_class,
        "deltas": deltas,
        "pricing_coverage": pricing_coverage(rows, table),
        "price_sources": _price_sources(headline),
        "marked_cells": _marked_cells(by_class),
        "warnings": _warnings(runs, by_class, headline, deltas, state),
        "runs": [_run_json(run) for run in sorted(runs, key=lambda item: item.row.run_id)],
    }


def render_bakeoff_markdown(report: Mapping[str, Any]) -> str:
    controls = report.get("controls", [])
    lines = [
        f"# WSB Bakeoff Report: {report.get('suite_id')}@{report.get('suite_version')}",
        "",
        f"- Run: `{report.get('suite_run_id')}` ({report.get('mode')} mode, "
        f"seed {report.get('seed')})   Generated: {report.get('generated_at')}",
        f"- Runs: {report.get('run_counts', {}).get('runs')}   Minimum attempted runs per "
        f"cell: {report.get('min_repetitions')}",
        "- Declared controls: "
        + ", ".join(
            f"`{control['name']}` = `<harness>+{control['provider_id']}`" for control in controls
        )
        + " (same harness, model profile, and task class)",
        f"- Marks: {_mark_summary(report.get('marked_cells', []))}",
        "",
        "Definitions:",
        "",
        *(f"- **{name}**: {text}" for name, text in report.get("definitions", {}).items()),
        "",
        render_pricing_coverage(report.get("pricing_coverage", {})),
        "",
        "## Task completion by arm",
        "",
        "Roll-up of each arm's task classes that are not under-sampled; see the per-class",
        "table for every cell, including the ones left out here.",
        "",
        _arm_table(report.get("headline", []), include_task_class=False),
        "",
        "## Task completion by arm and task class",
        "",
        _arm_table(report.get("by_task_class", []), include_task_class=True),
    ]
    for control in controls:
        rows = [row for row in report.get("deltas", []) if row.get("control") == control["name"]]
        lines.extend(
            [
                "",
                f"## Deltas vs {control['name']} (`<harness>+{control['provider_id']}`)",
                "",
                "Pooled over the task classes where both the arm and the control are",
                "publishable (listed per row):",
                "",
                _delta_table([row for row in rows if row["task_class"] == ALL_TASK_CLASSES]),
                "",
                "By task class:",
                "",
                _delta_table([row for row in rows if row["task_class"] != ALL_TASK_CLASSES]),
            ]
        )
    lines.extend(
        [
            "",
            "## Cost per successful task",
            "",
            _cost_table(report.get("headline", [])),
            "",
            *_cost_notes(report),
            "",
            "## Marked cells",
            "",
            _marked_table(report.get("marked_cells", [])),
            "",
            *(
                f"- `{mark}`: {text}"
                for mark, text in report.get("mark_legend", {}).items()
                if any(item.get("mark") == mark for item in report.get("marked_cells", []))
            ),
            "",
            "## Warnings",
            "",
        ]
    )
    warnings = report.get("warnings", [])
    lines.extend(
        [f"- ⚠ {warning['kind']}: {warning['message']}" for warning in warnings] or ["- none"]
    )
    lines.extend(["", "## Evidence index", "", _evidence_table(report.get("runs", [])), ""])
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Per-run records
# --------------------------------------------------------------------------


def _bakeoff_run(row: RunRow, table: PriceTable, link_base: Path) -> BakeoffRun:
    run_record = _optional_record(row.run_dir, "run.json")
    spawn = _optional_record(row.run_dir, SPAWN_METADATA_REF)
    audit = _mapping((spawn or {}).get("arm_audit"))
    disposition = _disposition(row, contaminated=audit.get("contaminated") is True)
    usage = row.token_usage
    source = str(usage.get("accounting_source") or "unknown")
    total = _total_tokens(usage)
    token_parts = None
    if source == "measured" and total is not None:
        token_status, tokens = "measured", total
        parts = {part: _int(usage.get(part)) for part in TOKEN_PARTS}
        if all(value is not None for value in parts.values()) and sum(parts.values()) == total:
            token_parts = parts
    else:
        token_status = "estimated" if source == "estimated" else "unknown"
        tokens = None
    model_id, model_id_source = _model_id(row.run_dir, spawn)
    if model_id is None and row.model_profile.startswith(OSS_MODEL_PREFIX):
        # An OSS cell's profile is the model it ran (runner model_id).
        model_id, model_id_source = row.model_profile, "model_profile"
    search_calls = _meter(_mapping((spawn or {}).get("process")).get("provider_calls"))
    cost, exclusions = _run_cost(
        row,
        run_record,
        table,
        token_status=token_status,
        model_id=model_id,
        search_calls=search_calls,
        native_tools=_native_tools(row.run_dir) if row.provider_id == "native" else [],
        codex_native_searches=(
            _codex_native_searches(row.run_dir)
            if row.provider_id == "native" and row.harness_id == "codex"
            else []
        ),
    )
    return BakeoffRun(
        row=row,
        arm_key=(row.harness_id, row.provider_id, row.model_profile),
        disposition=disposition,
        token_status=token_status,
        tokens=tokens,
        model_id=model_id,
        model_id_source=model_id_source,
        search_calls=search_calls,
        cost=cost,
        cost_exclusions=tuple(exclusions),
        evidence=_run_evidence(row, run_record, link_base),
        token_parts=token_parts,
    )


def _disposition(row: RunRow, *, contaminated: bool) -> str:
    # The arm audit is re-read even when the status disagrees: a leaked tool
    # call must never be scored, whatever the run record says.
    if contaminated or row.status == CONTAMINATED:
        return CONTAMINATED
    if row.status in NON_COMPARABLE_STATUSES:
        return NON_COMPARABLE
    if row.status == "succeeded":
        if row.evaluation_outcome == "pass":
            return SUCCESS
        if row.evaluation_outcome == "fail":
            return FAILURE
        return UNGRADED
    if row.status in TASK_FAILURE_STATUSES:
        return FAILURE
    if row.status in INFRASTRUCTURE_STATUSES:
        return row.status
    return UNCLASSIFIED


def _run_cost(
    row: RunRow,
    run_record: Mapping[str, Any] | None,
    table: PriceTable,
    *,
    token_status: str,
    model_id: str | None,
    search_calls: int,
    native_tools: list[tuple[str, bool]],
    codex_native_searches: Sequence[str] = (),
) -> tuple[dict[str, Any] | None, list[str]]:
    """Price one run with the WSB-04 cost model, or say why it cannot be priced."""

    if token_status != "measured":
        return None, [f"tokens:{token_status}"]
    usage, reason = _priceable_usage(row.token_usage)
    if usage is None:
        return None, [f"tokens:{reason}"]
    if (
        row.provider_id == "native"
        and row.harness_id == "codex"
        and len(codex_native_searches) > search_calls
    ):
        return None, ["vendor:codex-native:search_meter_mismatch"]
    calls: list[dict[str, Any]] = []
    for ref in _list((run_record or {}).get("provider_call_refs")):
        record = _optional_record(row.run_dir, ref) if isinstance(ref, str) else None
        if record is None:
            return None, [f"vendor:{row.provider_id}:provider_call_record_unreadable"]
        calls.append(vendor_call_from_record(record, provider_id=row.provider_id))
    if row.provider_id == "native" and row.harness_id == "claude-code":
        for tool, failed in native_tools:
            if tool not in {"WebSearch", "WebFetch"}:
                continue
            calls.append(
                vendor_call(
                    provider_id="claude-code-native",
                    status="not_applicable" if failed else "ok",
                    ended_at=(run_record or {}).get("ended_at"),
                    pricing_tier="search:request" if tool == "WebSearch" else "fetch:request",
                )
            )
    if row.provider_id == "native" and row.harness_id == "codex":
        # Priced at the Brave Search per-request rate (price table codex-native).
        for tier in codex_native_searches:
            calls.append(
                vendor_call(
                    provider_id="codex-native",
                    status="ok",
                    ended_at=(run_record or {}).get("ended_at"),
                    pricing_tier=tier,
                )
            )
    if search_calls > len(calls):
        # A live arm reaches its provider through MCP (or native search), which
        # writes no provider-call record. The calls happened; their spend is
        # unknown, so the run's total is unknown rather than model-only.
        unpriced = search_calls - len(calls)
        reasons = [f"vendor:{row.provider_id}:{unpriced}_search_calls_unpriced"]
        if row.provider_id == "native" and row.harness_id == "codex":
            transcript = _optional_record(row.run_dir, TRANSCRIPT_REF, expect=list)
            started, completed = set(), set()
            for event in transcript or []:
                payload = _mapping(
                    event.get("harness_event", event) if isinstance(event, Mapping) else {}
                )
                item = _mapping(payload.get("item"))
                if item.get("type") == "web_search" and isinstance(item.get("id"), str):
                    if payload.get("type") == "item.started":
                        started.add(item["id"])
                    elif payload.get("type") == "item.completed":
                        completed.add(item["id"])
            if started - completed:
                reasons.append("codex-native:uncompleted_search_items")
        model = model_cost(model_id, usage, table)
        if model["basis"] == "unknown":
            reasons.append(model["reason"])
        return None, reasons
    return (
        cell_cost(
            vendor_calls=calls,
            table=table,
            has_harness=True,
            model_id=model_id,
            token_usage=usage,
        ),
        [],
    )


def _native_tools(run_dir: Path | None) -> list[tuple[str, bool]]:
    """Deduplicate native calls by transcript call id, as the live meter does."""

    transcript = _optional_record(run_dir, TRANSCRIPT_REF, expect=list)
    seen: set[str] = set()
    tools: list[tuple[str, str]] = []
    failed_ids: set[str] = set()
    for index, event in enumerate(transcript or []):
        payload = event.get("harness_event", event) if isinstance(event, Mapping) else {}
        message = _mapping(_mapping(payload).get("message"))
        for block in _list(message.get("content")):
            if isinstance(block, Mapping) and block.get("type") == "tool_result":
                if block.get("is_error") is True and isinstance(block.get("tool_use_id"), str):
                    failed_ids.add(block["tool_use_id"])
        for offset, (call_id, name) in enumerate(_tool_call_entries(event)):
            key = call_id or f"event-{index}-{offset}"
            if key not in seen:
                seen.add(key)
                tools.append((name, key))
    return [(name, key in failed_ids) for name, key in tools]


# Codex `web_search` items carry an action. A search is a billable request; opening
# or searching within a fetched page adds no tool fee, as with Claude's WebFetch.
# An action codex does not expose ("other") is priced as a search, never dropped.
CODEX_FETCH_ACTIONS = frozenset({"open_page", "find_in_page"})


def _codex_native_searches(run_dir: Path | None) -> list[str]:
    """One pricing tier per completed Codex web_search item, deduplicated by id."""

    transcript = _optional_record(run_dir, TRANSCRIPT_REF, expect=list)
    actions: dict[str, str] = {}
    for index, event in enumerate(transcript or []):
        payload = _mapping(event.get("harness_event", event) if isinstance(event, Mapping) else {})
        item = _mapping(payload.get("item"))
        if payload.get("type") != "item.completed" or item.get("type") != "web_search":
            continue
        key = item.get("id") if isinstance(item.get("id"), str) else f"event-{index}"
        actions[key] = str(_mapping(item.get("action")).get("type") or "")
    return [
        "fetch:request" if action in CODEX_FETCH_ACTIONS else "search:request"
        for action in actions.values()
    ]


def _priceable_usage(usage: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Reshape measured usage into what ``cost_model.model_cost`` prices.

    The live driver writes disjoint buckets (reasoning moved out of output,
    cached reads out of input) that sum to ``total_billable``. The cost model
    expects input net of cached reads and reasoning inside output. Only a
    usage row whose buckets provably sum to its total is reshaped; anything
    else is ambiguous, and an ambiguous row is not priced.
    """

    input_tokens = _int(usage.get("input"))
    output_tokens = _int(usage.get("output"))
    if input_tokens is None or output_tokens is None:
        return None, "incomplete"
    cached = _int(usage.get("cached_input")) or 0
    reasoning = _int(usage.get("reasoning")) or 0
    total = _int(usage.get("total_billable"))
    if total is None and (cached or reasoning):
        return None, "buckets_not_disjoint"
    if total is not None and total != input_tokens + cached + output_tokens + reasoning:
        return None, "buckets_not_disjoint"
    priced_usage = {
        "accounting_source": "measured",
        "source_kind": usage.get("source_kind"),
        "input": input_tokens,
        "cached_input": cached,
        "output": output_tokens + reasoning,
    }
    for key in ("cache_write", "cache_write_5m", "cache_write_1h"):
        if key in usage:
            value = _int(usage.get(key))
            if value is None or value < 0:
                return None, "invalid_cache_write"
            priced_usage[key] = value
    writes = priced_usage.get("cache_write")
    if writes is None and (
        priced_usage.get("cache_write_5m", 0) or priced_usage.get("cache_write_1h", 0)
    ):
        return None, "invalid_cache_write"
    if writes is not None and writes > input_tokens:
        return None, "invalid_cache_write"
    if writes is not None and (
        priced_usage.get("cache_write_5m", 0) + priced_usage.get("cache_write_1h", 0) > writes
    ):
        return None, "invalid_cache_write"
    return priced_usage, None


def _model_id(
    run_dir: Path | None, spawn: Mapping[str, Any] | None
) -> tuple[str | None, str | None]:
    """The model a run was billed for: its spawn config, else what the harness reported."""

    configured = (spawn or {}).get("model_id")
    if isinstance(configured, str) and configured:
        origin = (spawn or {}).get("model_id_origin")
        return configured, f"spawn_config:{origin}" if isinstance(
            origin, str
        ) and origin else "spawn_config"
    transcript = _optional_record(run_dir, TRANSCRIPT_REF, expect=list)
    for entry in transcript or []:
        event = entry.get("harness_event") if isinstance(entry, dict) else None
        if not isinstance(event, dict):
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            reported = event.get("model")
        elif event.get("type") == "assistant":
            reported = _mapping(event.get("message")).get("model")
        else:
            continue
        if isinstance(reported, str) and reported:
            return reported, "harness_transcript"
    return None, None


def _run_evidence(
    row: RunRow, run_record: Mapping[str, Any] | None, link_base: Path
) -> dict[str, Any]:
    """Named evidence roles for one run: bundle, transcript, usage, verdict, calls."""

    run_dir = row.run_dir
    if run_dir is None:
        return {"bundle": None, "links": []}

    def link(ref: Any) -> str | None:
        if not isinstance(ref, str) or not (run_dir / ref).is_file():
            return None
        return _portable_link(run_dir / ref, link_base)

    record = run_record or {}
    return {
        "bundle": link(record.get("evidence_bundle_ref") or "evidence/bundle.yaml"),
        "run": link("run.json"),
        "transcript": link(TRANSCRIPT_REF),
        "harness_record": link(record.get("harness_ref")),
        "usage": link(record.get("metrics_ref")),
        "evaluation": link(record.get("evaluation_ref")),
        "spawn_metadata": link(SPAWN_METADATA_REF),
        "provider_calls": [
            item for item in (link(ref) for ref in _list(record.get("provider_call_refs"))) if item
        ],
        "links": list(row.evidence_links),
    }


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------


def _aggregate(
    runs: Sequence[BakeoffRun],
    *,
    arm_key: tuple[str, str, str],
    task_class: str,
    task_classes: Sequence[str] | None = None,
    oss_models: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    harness_id, provider_id, model_profile = arm_key
    dispositions = Counter(run.disposition for run in runs)
    attempted = [run for run in runs if run.disposition in ATTEMPTED]
    n = len(attempted)
    successes = dispositions[SUCCESS]
    ungraded = dispositions[UNGRADED]
    rate = successes / n if n and not ungraded else None
    graded = [run for run in attempted if run.disposition in (SUCCESS, FAILURE)]
    graded_successes = sum(1 for run in graded if run.disposition == SUCCESS)
    token_values = [float(run.tokens) for run in attempted if run.tokens is not None]
    graded_measured = [run for run in graded if run.tokens is not None]
    measured_successes = sum(1 for run in graded_measured if run.disposition == SUCCESS)
    mixed = [run.token_parts for run in attempted if run.token_parts is not None]
    token_counts = Counter(run.token_status for run in attempted)
    # An empty cell is not a thin sample; its other marks say why it is empty.
    thin = bool(n) and under_sampled(n)
    marked_runs = _marked_runs(runs, attempted, thin=thin)
    aggregate = {
        "arm": _arm_label(arm_key),
        "harness_id": harness_id,
        "provider_id": provider_id,
        "model_profile": model_profile,
        "task_class": task_class,
        "runs": len(runs),
        "metering": {
            "observed_n": sum(
                meter_observed(r.row.run_dir, r.row.provider_id, r.row.run_id) for r in runs
            ),
            "cells_n": len(runs),
        },
        "n": n,
        "successes": successes,
        "success_rate": rate,
        "success_ci_95": _wilson_ci(successes, n) if rate is not None else None,
        "rate_withheld": _rate_withheld(n, ungraded),
        "graded_n": len(graded),
        "graded_successes": graded_successes,
        "graded_success_rate": graded_successes / len(graded) if graded else None,
        "graded_success_ci_95": _wilson_ci(graded_successes, len(graded)) if graded else None,
        "ungraded_n": ungraded,
        "under_sampled": thin,
        "tokens": {
            "per_task": statistics.fmean(token_values) if token_values else None,
            "p50": _percentile(token_values, 50),
            "measured_n": len(token_values),
            "attempted_n": n,
            "unknown_n": token_counts["unknown"],
            "estimated_n": token_counts["estimated"],
            "per_success": (
                sum(float(run.tokens) for run in graded_measured) / measured_successes
                if measured_successes
                else None
            ),
            "per_success_n": len(graded_measured),
            "per_success_successes_n": measured_successes,
            "mix": (
                {
                    "input": statistics.fmean(part["input"] for part in mixed),
                    "cached_input": statistics.fmean(part["cached_input"] for part in mixed),
                    "output": statistics.fmean(
                        part["output"] + part["reasoning"] for part in mixed
                    ),
                }
                if mixed
                else None
            ),
            "mix_n": len(mixed),
        },
        "latency_p50_ms": _percentile(_latency_values(run.row for run in attempted), 50),
        "cost": _cost_summary(attempted, ungraded=ungraded),
        "marks": {mark: len(ids) for mark, ids in marked_runs.items()},
        "marked_runs": marked_runs,
        "run_ids": sorted(run.row.run_id for run in runs),
        "evidence": _aggregate_evidence(runs),
    }
    if task_classes is not None:
        aggregate["task_classes"] = list(task_classes)
    rate = _model_rate(model_profile, oss_models)
    if rate is not None:
        aggregate["model_rate"] = rate
    return aggregate


def _model_rate(model_profile: str, oss_models: Mapping[str, Mapping[str, Any]]) -> dict[str, Any] | None:
    """The OSS catalog rate an arm's model is priced at; None for frontier models."""

    if not model_profile.startswith(OSS_MODEL_PREFIX):
        return None
    entry = oss_models.get(model_profile[len(OSS_MODEL_PREFIX):]) or {}
    return {
        "model_id": model_profile,
        "rate_basis": entry.get("rate_basis"),
        "price_source": entry.get("source"),
        "price_as_of": entry.get("as_of"),
    }


def _marked_runs(
    runs: Sequence[BakeoffRun], attempted: Sequence[BakeoffRun], *, thin: bool
) -> dict[str, list[str]]:
    """Each mark a cell carries, with the runs it applies to."""

    marked: dict[str, list[str]] = defaultdict(list)
    for run in runs:
        if run.disposition not in (SUCCESS, FAILURE):
            marked[run.disposition].append(run.row.run_id)
        if any(item.startswith(TELEMETRY_INCOMPLETE) for item in run.row.telemetry_warnings):
            marked[TELEMETRY_INCOMPLETE].append(run.row.run_id)
    for run in attempted:
        if run.token_status == "unknown":
            marked[UNKNOWN_TOKENS].append(run.row.run_id)
        elif run.token_status == "estimated":
            marked[ESTIMATED_TOKENS].append(run.row.run_id)
        elif _unpriced(run):
            marked[UNKNOWN_COST].append(run.row.run_id)
        if thin:
            marked[UNDER_SAMPLED].append(run.row.run_id)
    return {mark: sorted(ids) for mark, ids in sorted(marked.items())}


def _unpriced(run: BakeoffRun) -> bool:
    """Measured tokens but no price: unpriced search, ambiguous buckets, or no rate."""

    return run.token_status == "measured" and (run.cost is None or run.cost["total_usd"] is None)


def _rate_withheld(n: int, ungraded: int) -> str | None:
    if n == 0:
        return "no_attempted_runs"
    if ungraded:
        return UNGRADED
    return None


def _cost_summary(attempted: Sequence[BakeoffRun], *, ungraded: int) -> dict[str, Any]:
    priced = [run for run in attempted if run.cost is not None]
    summary = summarize_arm_costs(
        {"arm": "cell", "success": run.disposition == SUCCESS, "cost": run.cost} for run in priced
    ).get("cell")
    reasons = {reason for run in attempted for reason in run.cost_exclusions}
    reasons.update((summary or {}).get("unknown_reasons", []))
    costed = summary["costed_cells"] if summary else 0
    if not costed:
        per_success, status = None, "no_costed_runs"
    elif ungraded:
        # Ungraded runs are not successes, so dividing by the graded passes
        # would overstate the price of each success.
        per_success, status = None, "withheld_ungraded"
    else:
        per_success, status = summary["usd_per_success"], summary["usd_per_success_status"]
    return {
        "attempted_n": len(attempted),
        "costed_n": costed,
        "excluded_n": len(attempted) - costed,
        "unknown_cost_n": sum(1 for run in attempted if _unpriced(run)),
        "exclusion_reasons": sorted(reasons),
        "total_usd": summary["total_usd"] if summary and costed else None,
        "usd_per_task": summary["usd_per_task"] if summary and costed else None,
        "usd_per_success": per_success,
        "usd_per_success_status": status,
        "successes_costed": summary["successes"] if summary else 0,
        "basis": summary["basis"] if summary and costed else "unknown",
        "price_sources": summary["price_sources"] if summary and costed else [],
        "in_cost_table": bool(costed),
    }


def _headline(
    arm_key: tuple[str, str, str],
    cells: Mapping[tuple[tuple[str, str, str], str], Mapping[str, Any]],
    grouped: Mapping[tuple[tuple[str, str, str], str], Sequence[BakeoffRun]],
    oss_models: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    classes = sorted(task_class for key, task_class in cells if key == arm_key)
    included = [c for c in classes if _publishable(cells[(arm_key, c)])]
    runs = [run for c in included for run in grouped[(arm_key, c)]]
    row = _aggregate(runs, arm_key=arm_key, task_class=ALL_TASK_CLASSES, task_classes=included,
                     oss_models=oss_models)
    row["excluded_task_classes"] = {
        c: _exclusion_reasons(cells[(arm_key, c)]) for c in classes if c not in included
    }
    if not included:
        row["rate_withheld"] = NO_PUBLISHABLE_CLASS
    return row


def _publishable(cell: Mapping[str, Any]) -> bool:
    """A cell may inform a headline or delta: it has attempts and enough of them."""

    return bool(cell["n"]) and not cell["under_sampled"]


def _exclusion_reasons(cell: Mapping[str, Any]) -> list[str]:
    if not cell["n"]:
        return ["no_attempted_runs", *sorted(cell["marks"])]
    return [UNDER_SAMPLED]


# --------------------------------------------------------------------------
# Deltas against declared controls
# --------------------------------------------------------------------------


def _deltas(
    arm_keys: Sequence[tuple[str, str, str]],
    cells: Mapping[tuple[tuple[str, str, str], str], Mapping[str, Any]],
    grouped: Mapping[tuple[tuple[str, str, str], str], Sequence[BakeoffRun]],
    controls: Sequence[Control],
    oss_models: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for position, control in enumerate(controls):
        # A control is compared with the controls declared before it (native
        # vs no-search), never with itself or in mirror image.
        skip = {item.provider_id for item in controls[: position + 1]}
        for arm_key in arm_keys:
            harness_id, provider_id, model_profile = arm_key
            if provider_id in skip:
                continue
            control_key = (harness_id, control.provider_id, model_profile)
            classes = sorted(c for key, c in cells if key == arm_key)
            pooled: list[str] = []
            for task_class in classes:
                subject = cells[(arm_key, task_class)]
                baseline = cells.get((control_key, task_class))
                rows.append(
                    _delta_row(control, arm_key, control_key, task_class, subject, baseline)
                )
                if baseline is not None and _publishable(subject) and _publishable(baseline):
                    pooled.append(task_class)
            # The roll-up pools only the classes where both sides are
            # publishable, so the arm and its control share one denominator.
            subject_all = _aggregate(
                [run for c in pooled for run in grouped[(arm_key, c)]],
                arm_key=arm_key,
                task_class=ALL_TASK_CLASSES,
                task_classes=pooled,
                oss_models=oss_models,
            )
            baseline_all = (
                _aggregate(
                    [run for c in pooled for run in grouped[(control_key, c)]],
                    arm_key=control_key,
                    task_class=ALL_TASK_CLASSES,
                    task_classes=pooled,
                    oss_models=oss_models,
                )
                if any((control_key, c) in cells for c in classes)
                else None
            )
            row = _delta_row(
                control, arm_key, control_key, ALL_TASK_CLASSES, subject_all, baseline_all
            )
            row["task_classes"] = pooled
            if not pooled:
                row["marks"] = [
                    "no_task_class_comparable",
                    *(["control_missing"] if baseline_all is None else []),
                ]
            rows.append(row)
    return rows


def _delta_row(
    control: Control,
    arm_key: tuple[str, str, str],
    control_key: tuple[str, str, str],
    task_class: str,
    subject: Mapping[str, Any],
    baseline: Mapping[str, Any] | None,
) -> dict[str, Any]:
    marks: list[str] = []
    for side, cell in (("subject", subject), ("control", baseline)):
        if cell is None:
            marks.append(f"{side}_missing")
        elif not cell["n"]:
            marks.append(f"{side}_no_attempted_runs")
        elif cell["under_sampled"]:
            marks.append(f"{side}_under_sampled")
    comparable = not marks
    success_delta = success_ci = None
    if comparable:
        for side, cell in (("subject", subject), ("control", baseline)):
            if cell["success_rate"] is None:
                marks.append(f"{side}_rate_withheld:{cell['rate_withheld']}")
        if subject["success_rate"] is not None and baseline["success_rate"] is not None:
            success_delta = subject["success_rate"] - baseline["success_rate"]
            success_ci = newcombe_interval(
                subject["successes"], subject["n"], baseline["successes"], baseline["n"]
            )
    token_ratio = token_delta = per_success_ratio = None
    if comparable:
        subject_per_success = subject["tokens"]["per_success"]
        control_per_success = baseline["tokens"]["per_success"]
        if subject_per_success is not None and control_per_success:
            per_success_ratio = subject_per_success / control_per_success
        subject_tokens = subject["tokens"]["per_task"]
        control_tokens = baseline["tokens"]["per_task"]
        if subject_tokens is None:
            marks.append("subject_tokens_unmeasured")
        if control_tokens is None:
            marks.append("control_tokens_unmeasured")
        elif control_tokens == 0:
            marks.append("control_zero_tokens")
        if subject_tokens is not None and control_tokens:
            token_ratio = subject_tokens / control_tokens
            token_delta = subject_tokens - control_tokens
        for side, cell in (("subject", subject), ("control", baseline)):
            tokens = cell["tokens"]
            if tokens["per_task"] is not None and tokens["measured_n"] < tokens["attempted_n"]:
                marks.append(f"{side}_token_coverage_partial")
    return {
        "control": control.name,
        "control_provider_id": control.provider_id,
        "harness_id": arm_key[0],
        "model_profile": arm_key[2],
        "task_class": task_class,
        "arm": _arm_label(arm_key),
        "control_arm": _arm_label(control_key),
        "comparable": comparable,
        "subject": _delta_side(subject),
        "baseline": _delta_side(baseline),
        "success_delta": success_delta,
        "success_delta_ci_95": success_ci,
        "token_ratio": token_ratio,
        "token_delta_per_task": token_delta,
        "tokens_per_success_ratio": per_success_ratio,
        "token_denominator": (
            "measured tokens per attempted task: "
            f"{_arm_label(arm_key)} {_side_tokens_text(subject)} / "
            f"{_arm_label(control_key)} {_side_tokens_text(baseline)}"
        ),
        "marks": marks,
        "evidence": [
            *(subject or {}).get("evidence", []),
            *((baseline or {}).get("evidence", [])),
        ],
    }


def _delta_side(cell: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if cell is None:
        return None
    return {
        "n": cell["n"],
        "successes": cell["successes"],
        "success_rate": cell["success_rate"],
        "success_ci_95": cell["success_ci_95"],
        "tokens_per_task": cell["tokens"]["per_task"],
        "tokens_measured_n": cell["tokens"]["measured_n"],
        "tokens_per_success": cell["tokens"]["per_success"],
        "graded_success_rate": cell["graded_success_rate"],
        "graded_n": cell["graded_n"],
        "run_ids": cell["run_ids"],
    }


def newcombe_interval(
    successes_a: int, n_a: int, successes_b: int, n_b: int
) -> dict[str, float] | None:
    """95% interval for ``p_a - p_b`` (Newcombe 1998, method 10: hybrid Wilson score)."""

    if not n_a or not n_b:
        return None
    p_a, p_b = successes_a / n_a, successes_b / n_b
    low_a, high_a = _wilson_bounds(successes_a, n_a)
    low_b, high_b = _wilson_bounds(successes_b, n_b)
    delta = p_a - p_b
    return {
        "low": delta - math.sqrt((p_a - low_a) ** 2 + (high_b - p_b) ** 2),
        "high": delta + math.sqrt((high_a - p_a) ** 2 + (p_b - low_b) ** 2),
    }


def _wilson_bounds(successes: int, n: int) -> tuple[float, float]:
    interval = _wilson_ci(successes, n)
    return float(interval["low"]), float(interval["high"])


# --------------------------------------------------------------------------
# Marks and warnings
# --------------------------------------------------------------------------


def _marked_cells(cells: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    marked = []
    for cell in cells:
        for mark, run_ids in cell["marked_runs"].items():
            marked.append(
                {
                    "mark": mark,
                    "arm": cell["arm"],
                    "task_class": cell["task_class"],
                    "runs_affected": len(run_ids),
                    "n": cell["n"],
                    "run_ids": run_ids,
                    "evidence": [item for item in cell["evidence"] if item["run_id"] in run_ids],
                }
            )
    return sorted(marked, key=lambda item: (item["mark"], item["arm"], item["task_class"]))


def _warnings(
    runs: Sequence[BakeoffRun],
    by_class: Sequence[Mapping[str, Any]],
    headline: Sequence[Mapping[str, Any]],
    deltas: Sequence[Mapping[str, Any]],
    state: Mapping[str, Any],
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    minimum = sew_report.MIN_COMPARABLE_REPETITIONS
    for cell in by_class:
        where = f"{cell['arm']} / {cell['task_class']}"
        marks = cell["marks"]
        for mark, provenance in ((UNKNOWN_TOKENS, "unknown"), (ESTIMATED_TOKENS, "estimated")):
            if mark in marks:
                warnings.append(
                    _warning(
                        mark,
                        f"{where}: {_runs(marks[mark])} of {cell['n']} attempted "
                        f"{'has' if marks[mark] == 1 else 'have'} {provenance} token usage; "
                        "excluded from token and cost tables, not counted as zero",
                    )
                )
        if UNKNOWN_COST in marks:
            warnings.append(
                _warning(
                    UNKNOWN_COST,
                    f"{where}: {_runs(marks[UNKNOWN_COST])} with measured tokens could not be "
                    f"priced ({', '.join(cell['cost']['exclusion_reasons'])})",
                )
            )
        if UNDER_SAMPLED in marks:
            warnings.append(
                _warning(
                    UNDER_SAMPLED,
                    f"{where}: n={cell['n']} attempted (< {minimum}); kept out of the headline "
                    "and deltas",
                )
            )
        if UNGRADED in marks:
            warnings.append(
                _warning(
                    UNGRADED,
                    f"{where}: {_runs(marks[UNGRADED])} delivered with no pass/fail verdict; "
                    "completion rate withheld",
                )
            )
        for mark in (CONTAMINATED, NON_COMPARABLE, UNCLASSIFIED, *sorted(INFRASTRUCTURE_STATUSES)):
            if mark in marks:
                warnings.append(
                    _warning(mark, f"{where}: {_runs(marks[mark])} excluded from rates ({mark})")
                )
    for row in headline:
        cost = row["cost"]
        if not cost["in_cost_table"]:
            reasons = ", ".join(cost["exclusion_reasons"]) or (
                "no publishable task class" if not row["task_classes"] else "no attempted runs"
            )
            warnings.append(
                _warning(
                    "not_in_cost_table",
                    f"{row['arm']}: excluded from the cost table, never shown as $0; no attempted "
                    f"run in its headline classes has a known cost ({reasons})",
                )
            )
        elif cost["excluded_n"]:
            warnings.append(
                _warning(
                    "cost_partial",
                    f"{row['arm']}: {cost['excluded_n']} of {cost['attempted_n']} attempted runs "
                    f"excluded from cost ({', '.join(cost['exclusion_reasons'])})",
                )
            )
    missing_controls = sorted(
        {
            (row["control"], row["control_arm"])
            for row in deltas
            if "control_missing" in row["marks"]
        }
    )
    for control, arm in missing_controls:
        warnings.append(
            _warning(
                "missing_control",
                f"control {control} has no {arm} runs for some task classes; those deltas "
                "are marked, not computed",
            )
        )
    for run in runs:
        for item in run.row.telemetry_warnings:
            if item.startswith(TELEMETRY_INCOMPLETE):
                warnings.append(_warning(TELEMETRY_INCOMPLETE, f"{run.row.run_id}: {item}"))
    summary = state.get("summary")
    if isinstance(summary, Mapping) and summary.get("remaining_cells"):
        warnings.append(
            _warning(
                "suite_incomplete",
                f"suite stopped with {summary['remaining_cells']} cells not run "
                f"({summary.get('stopped_reason') or 'no reason recorded'})",
            )
        )
    return sew_report._unique_warning_dicts(warnings)


def _warning(kind: str, message: str) -> dict[str, str]:
    return {"kind": kind, "message": message}


def _runs(count: int) -> str:
    return f"{count} run" if count == 1 else f"{count} runs"


def _price_sources(headline: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    sources = {
        (source["price_source"], source["price_as_of"])
        for row in headline
        for source in row["cost"]["price_sources"]
    }
    return [{"price_source": src, "price_as_of": as_of} for src, as_of in sorted(sources)]


def _run_json(run: BakeoffRun) -> dict[str, Any]:
    row = run.row
    return {
        "run_id": row.run_id,
        "arm": _arm_label(run.arm_key),
        "task_id": row.task_id,
        "task_class": row.task_class,
        "repetition": row.repetition,
        "status": row.status,
        "evaluation_outcome": row.evaluation_outcome,
        "disposition": run.disposition,
        "token_status": run.token_status,
        "tokens": run.tokens,
        "search_calls": run.search_calls,
        "model_id": run.model_id,
        "model_id_source": run.model_id_source,
        "cost_usd": run.cost["total_usd"] if run.cost else None,
        "cost_basis": run.cost["basis"] if run.cost else "unknown",
        "cost_pricing_notes": sorted(
            {
                component["pricing_note"]
                for component in (run.cost or {}).get("components", [])
                if component.get("pricing_note")
            }
        ),
        "cost_exclusions": [*run.cost_exclusions, *((run.cost or {}).get("unknown_reasons", []))],
        "evidence": dict(run.evidence),
    }


# --------------------------------------------------------------------------
# Loading helpers
# --------------------------------------------------------------------------


def _task_classes(
    module_base: Path, state: Mapping[str, Any], index: Iterable[Mapping[str, Any]]
) -> dict[str, str]:
    """Task classes from the task manifests, then the WSB production catalog."""

    classes = _load_task_classes(module_base, state, index)
    if "unknown" in classes.values():
        production = _production_task_classes(module_base)
        for task_id, task_class in classes.items():
            if task_class == "unknown" and task_id in production:
                classes[task_id] = production[task_id]
    return classes


def _production_task_classes(module_base: Path) -> dict[str, str]:
    try:
        catalog = load_production_catalog(module_base)["catalog"]
    except (OSError, SchemaError, yaml.YAMLError):
        return {}
    return {
        str(task["id"]): str(task["task_class"])
        for task in _list(catalog.get("tasks"))
        if isinstance(task, dict)
        and isinstance(task.get("id"), str)
        and isinstance(task.get("task_class"), str)
    }


def _optional_record(run_dir: Path | None, ref: str, *, expect: type = dict) -> Any:
    if run_dir is None:
        return None
    try:
        value = load_record(run_dir, ref)
    except (OSError, SchemaError):
        return None
    return value if isinstance(value, expect) else None


def _aggregate_evidence(runs: Iterable[BakeoffRun]) -> list[dict[str, Any]]:
    return [
        {"run_id": run.row.run_id, "bundle": run.evidence.get("bundle")}
        for run in sorted(runs, key=lambda item: item.row.run_id)
    ]


def _sorted_arm_keys(
    keys: Iterable[tuple[str, str, str]], controls: Sequence[Control]
) -> list[tuple[str, str, str]]:
    rank = {control.provider_id: position + 1 for position, control in enumerate(controls)}
    return sorted(keys, key=lambda key: (key[0], key[2], rank.get(key[1], 0), key[1]))


def _validate_controls(controls: Sequence[Control]) -> tuple[Control, ...]:
    declared = tuple(controls)
    names = [control.name for control in declared]
    providers = [control.provider_id for control in declared]
    if not declared or len(set(names)) != len(names) or len(set(providers)) != len(providers):
        raise ReportError("controls must be a non-empty list of distinct names and provider ids")
    return declared


def _arm_label(arm_key: tuple[str, str, str]) -> str:
    harness_id, provider_id, model_profile = arm_key
    label = f"{harness_id}+{provider_id}"
    return label if model_profile in ("", "default") else f"{label} [{model_profile}]"


def _meter(value: Any) -> int:
    count = _int(value)
    return count if count is not None else 0


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


# --------------------------------------------------------------------------
# Markdown rendering
# --------------------------------------------------------------------------


def _arm_table(rows: Sequence[Mapping[str, Any]], *, include_task_class: bool) -> str:
    headers = [
        "arm",
        *(["task_class"] if include_task_class else ["task classes"]),
        "n",
        "metering coverage",
        "success",
        "95% CI",
        "tokens/task (measured/n)",
        "tokens/success",
        "token mix: in / cache read / out",
        "$/success",
        "p50",
        "marks",
        "evidence",
    ]
    return _table(
        headers,
        [
            [
                row["arm"],
                row["task_class"]
                if include_task_class
                else ", ".join(row.get("task_classes", [])) or "none publishable",
                row["n"],
                f"{row['metering']['observed_n']}/{row['metering']['cells_n']}"
                if row["provider_id"] in PROVIDER_SERVER_NAMES
                else "n/a",
                _rate_text(row),
                _ci(row["success_ci_95"]),
                _tokens_text(row["tokens"]),
                _tokens_per_success_text(row["tokens"], row["graded_n"]),
                _token_mix_text(row["tokens"]),
                _per_success_text(row["cost"]),
                _seconds(row["latency_p50_ms"]),
                _marks_text(row["marks"]) or "-",
                _evidence_text(row["evidence"]),
            ]
            for row in rows
        ],
    )


def _delta_table(rows: Sequence[Mapping[str, Any]]) -> str:
    return _table(
        [
            "harness",
            "task_class",
            "arm",
            "arm success (n)",
            "control success (n)",
            "success delta (95% CI)",
            "arm tokens/task (measured/n)",
            "control tokens/task (measured/n)",
            "token ratio (arm / control)",
            "tokens/success ratio (arm / control)",
            "marks",
            "evidence",
        ],
        [
            [
                row["harness_id"]
                if row["model_profile"] in ("", "default")
                else f"{row['harness_id']} [{row['model_profile']}]",
                (", ".join(row["task_classes"]) or "none")
                if row["task_class"] == ALL_TASK_CLASSES
                else row["task_class"],
                row["arm"],
                _side_rate_text(row["subject"]),
                _side_rate_text(row["baseline"]),
                _success_delta_text(row),
                _side_tokens_cell(row["subject"]),
                _side_tokens_cell(row["baseline"]),
                _ratio_text(row["token_ratio"]),
                _ratio_text(row.get("tokens_per_success_ratio")),
                ", ".join(row["marks"]) or "-",
                _evidence_text(row["evidence"]),
            ]
            for row in rows
        ],
    )


def _cost_table(rows: Sequence[Mapping[str, Any]]) -> str:
    if any("model_rate" in row for row in rows):
        return _oss_cost_table(rows)
    return _table(
        ["arm", "costed / attempted", "successes (costed)", "$/task", "$/success", "basis"],
        [
            [
                row["arm"],
                f"{row['cost']['costed_n']}/{row['cost']['attempted_n']}",
                row["cost"]["successes_costed"],
                _cost_usd(row["cost"]["usd_per_task"], row["cost"]["basis"]),
                _per_success_text(row["cost"]),
                row["cost"]["basis"],
            ]
            for row in rows
            if row["cost"]["in_cost_table"]
        ],
    )


def _oss_cost_table(rows: Sequence[Mapping[str, Any]]) -> str:
    """The cost table once OSS models are present: harness and model are columns,
    and an OSS model's dollars name the rate basis they were priced at."""

    def usd(row: Mapping[str, Any], text: str) -> str:
        rate = row.get("model_rate")
        return f"{text} ({_rate_basis_text(rate)})" if rate and text.startswith("$") else text

    return _table(
        ["harness", "model", "arm", "costed / attempted", "successes (costed)", "$/task", "$/success", "basis"],
        [
            [
                row["harness_id"],
                row["model_rate"]["model_id"] if "model_rate" in row else _profile_text(row["model_profile"]),
                row["provider_id"],
                f"{row['cost']['costed_n']}/{row['cost']['attempted_n']}",
                row["cost"]["successes_costed"],
                usd(row, _cost_usd(row["cost"]["usd_per_task"], row["cost"]["basis"])),
                usd(row, _per_success_text(row["cost"])),
                row["cost"]["basis"],
            ]
            for row in rows
            if row["cost"]["in_cost_table"]
        ],
    )


def _rate_basis_text(rate: Mapping[str, Any]) -> str:
    if rate["rate_basis"] == "self-hosted":
        return "self-hosted"
    if rate["rate_basis"] == "list":
        return f"catalog {rate['price_as_of']}"
    return "no catalog rate"


def _profile_text(model_profile: str) -> str:
    return "harness default" if model_profile in ("", "default") else model_profile


def _oss_rate_notes(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    rates = {row["model_rate"]["model_id"]: row["model_rate"] for row in rows if "model_rate" in row}
    if not rates:
        return []
    lines = []
    for model_id, rate in sorted(rates.items()):
        if rate["rate_basis"] == "self-hosted":
            text = "self-hosted, priced at $0 per token; hardware and power are not counted"
        elif rate["rate_basis"] == "list":
            text = f"catalog list rate as of {rate['price_as_of']} ({rate['price_source']})"
        else:
            text = "not in the OSS catalog, so its cost is unknown"
        lines.append(f"- {model_id}: {text}")
    return ["", "OSS model rates (config/oss-models.yaml):", *lines]


def _cost_notes(report: Mapping[str, Any]) -> list[str]:
    notes = [
        f"- ⚠ {warning['message']}"
        for warning in report.get("warnings", [])
        if warning["kind"] in {"not_in_cost_table", "cost_partial", UNKNOWN_TOKENS, UNKNOWN_COST}
    ]
    if not notes:
        notes = ["- every attempted run has a known cost"]
    sources = report.get("price_sources", [])
    if sources:
        notes += [
            "",
            "Price sources:",
            *(f"- {item['price_source']} (as of {item['price_as_of']})" for item in sources),
        ]
    proxy_notes = sorted(
        {
            note
            for run in report.get("runs", [])
            for note in run.get("cost_pricing_notes", [])
            if note.startswith("proxy_rate:")
        }
    )
    if proxy_notes:
        notes += ["", "Operator proxy rates:", *(f"- {note}" for note in proxy_notes)]
    notes += _oss_rate_notes(report.get("headline", []))
    return notes or ["- every attempted run has a known cost"]


def _marked_table(rows: Sequence[Mapping[str, Any]]) -> str:
    return _table(
        ["mark", "arm", "task_class", "runs affected", "n attempted", "evidence"],
        [
            [
                row["mark"],
                row["arm"],
                row["task_class"],
                row["runs_affected"],
                row["n"],
                _evidence_text(row["evidence"]),
            ]
            for row in rows
        ],
    )


def _evidence_table(runs: Sequence[Mapping[str, Any]]) -> str:
    return _table(
        [
            "run_id",
            "arm",
            "task",
            "status",
            "disposition",
            "tokens",
            "cost",
            "bundle",
            "transcript",
            "usage",
            "evaluation",
            "provider calls",
        ],
        [
            [
                run["run_id"],
                run["arm"],
                run["task_id"],
                run["status"],
                run["disposition"],
                _num_text(run["tokens"]) if run["tokens"] is not None else run["token_status"],
                _cost_usd(run["cost_usd"], run["cost_basis"])
                if run["cost_usd"] is not None
                else "unknown",
                _link("bundle", run["evidence"].get("bundle")),
                _link("transcript", run["evidence"].get("transcript")),
                _link("usage", run["evidence"].get("usage")),
                _link("evaluation", run["evidence"].get("evaluation")),
                "<br>".join(
                    _link(f"call {index + 1}", link)
                    for index, link in enumerate(run["evidence"].get("provider_calls", []))
                )
                or "-",
            ]
            for run in runs
        ],
    )


def _mark_summary(marked: Sequence[Mapping[str, Any]]) -> str:
    cells = Counter(item["mark"] for item in marked)
    if not cells:
        return "none"
    return "   ".join(
        f"⚠ {count} {mark} cell{'' if count == 1 else 's'}" for mark, count in sorted(cells.items())
    )


def _rate_text(row: Mapping[str, Any]) -> str:
    if row["success_rate"] is None:
        return f"withheld ({row['rate_withheld']}){_graded_rate_suffix(row)}"
    return f"{_pct(row['success_rate'])} ({row['successes']}/{row['n']})"


def _graded_rate_suffix(row: Mapping[str, Any]) -> str:
    """The outcome rate over graded runs is always shown, with what it leaves out."""

    if not row.get("graded_n"):
        return ""
    return (
        f"; graded {_pct(row['graded_success_rate'])} "
        f"({row['graded_successes']}/{row['graded_n']}, {row['ungraded_n']} ungraded)"
    )


def _side_rate_text(side: Mapping[str, Any] | None) -> str:
    if side is None:
        return "missing"
    if side["success_rate"] is None:
        if side.get("graded_n"):
            return f"withheld; graded {_pct(side['graded_success_rate'])} (n={side['graded_n']})"
        return f"withheld (n={side['n']})"
    return f"{_pct(side['success_rate'])} (n={side['n']})"


def _success_delta_text(row: Mapping[str, Any]) -> str:
    if row["success_delta"] is None:
        return "-"
    return f"{_signed_pct(row['success_delta'])} ({_signed_interval(row['success_delta_ci_95'])})"


def _signed_interval(interval: Mapping[str, float] | None) -> str:
    if not interval:
        return "-"
    return f"{interval['low'] * 100:+.1f} to {interval['high'] * 100:+.1f}"


def _tokens_text(tokens: Mapping[str, Any]) -> str:
    if tokens["per_task"] is None:
        return f"unmeasured (0/{tokens['attempted_n']})"
    return f"{tokens['per_task']:,.0f} ({tokens['measured_n']}/{tokens['attempted_n']})"


def _tokens_per_success_text(tokens: Mapping[str, Any], graded_n: int) -> str:
    if tokens["per_success"] is None:
        return "-"
    return (
        f"{tokens['per_success']:,.0f} "
        f"({tokens['per_success_successes_n']} measured successes / "
        f"{tokens['per_success_n']} measured of {graded_n} graded)"
    )


def _token_mix_text(tokens: Mapping[str, Any]) -> str:
    mix = tokens.get("mix")
    if not mix:
        return "-"
    return (
        f"{mix['input']:,.0f} / {mix['cached_input']:,.0f} / {mix['output']:,.0f}"
        f" ({tokens['mix_n']}/{tokens['attempted_n']})"
    )


def _side_tokens_cell(side: Mapping[str, Any] | None) -> str:
    if side is None:
        return "missing"
    if side["tokens_per_task"] is None:
        return f"unmeasured (0/{side['n']})"
    return f"{side['tokens_per_task']:,.0f} ({side['tokens_measured_n']}/{side['n']})"


def _side_tokens_text(cell: Mapping[str, Any] | None) -> str:
    if cell is None:
        return "(missing)"
    tokens = cell["tokens"]
    per_task = "unmeasured" if tokens["per_task"] is None else f"{tokens['per_task']:,.0f}"
    return f"{per_task} over {tokens['measured_n']} of {tokens['attempted_n']} runs"


def _ratio_text(ratio: float | None) -> str:
    if ratio is None:
        return "-"
    if ratio == 0:
        return "0.00x"
    if ratio < 1:
        return f"{ratio:.2f}x ({1 / ratio:.1f}x fewer)"
    if ratio > 1:
        return f"{ratio:.2f}x ({ratio:.1f}x more)"
    return "1.00x (no change)"


def _per_success_text(cost: Mapping[str, Any]) -> str:
    status = cost["usd_per_success_status"]
    if cost["usd_per_success"] is not None:
        return _cost_usd(cost["usd_per_success"], cost["basis"])
    if status == "undefined_zero_successes":
        return "undefined (0 successes)"
    if status == "withheld_ungraded":
        return "withheld (ungraded)"
    return "unknown (see cost)"


def _marks_text(marks: Mapping[str, int]) -> str:
    return ", ".join(f"{mark} ({count})" for mark, count in marks.items())


def _evidence_text(evidence: Sequence[Mapping[str, Any]]) -> str:
    parts = [
        _link(str(item["run_id"]), item["bundle"])
        if item.get("bundle")
        else f"{_md_cell(item['run_id'])} (no bundle)"
        for item in evidence[:EVIDENCE_LINKS_SHOWN]
    ]
    if len(evidence) > EVIDENCE_LINKS_SHOWN:
        parts.append(f"+{len(evidence) - EVIDENCE_LINKS_SHOWN} more in {JSON_REPORT_NAME}")
    return "<br>".join(parts) or "-"


def _link(label: str, target: Any) -> str:
    return f"[{_md_cell(label)}]({target})" if isinstance(target, str) and target else "-"


def _num_text(value: Any) -> str:
    return "-" if not sew_report._is_number(value) else f"{value:,.0f}"


def _usd(value: Any) -> str:
    return "-" if not sew_report._is_number(value) else f"${value:.4f}"


def _cost_usd(value: Any, basis: str) -> str:
    amount = _usd(value)
    return f"{amount} (estimated)" if basis == "estimated" and amount != "-" else amount


def _seconds(value: Any) -> str:
    return "-" if not sew_report._is_number(value) else f"{value / 1000:.1f}s"


__all__ = [
    "Control",
    "DEFAULT_CONTROLS",
    "JSON_REPORT_NAME",
    "MARKDOWN_REPORT_NAME",
    "build_bakeoff_report",
    "generate_bakeoff_report",
    "newcombe_interval",
    "render_bakeoff_markdown",
]
