"""Harness drivers for Codex and Claude Code.

``run_harness`` is the entrypoint and dispatches on ``HarnessRunConfig.mode``:

* ``fixture`` (the default, and the only CI path) writes a deterministic
  evidence bundle without network or hosted-harness access.
* ``live`` (WSB-05) really spawns the harness CLI through ``sew.live_harness``,
  which is operator-gated behind ``SEW_HARNESS_LIVE=1`` and never runs in CI.

Both modes write the same SEW-01 evidence bundle and share one terminal-status
vocabulary, so a boot flake and a wrong answer stay different findings.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import yaml

from .catalog import module_root
from .metrics import normalize_run_metrics
from .schema import PROVIDERS, SchemaError, validate_fixture_run

HarnessId = Literal["codex", "claude-code"]
RunStatus = Literal[
    "succeeded",
    "failed",
    "timeout",
    "unsupported",
    "cancelled",
    "harness_boot_failed",
    "provider_unavailable",
    "budget_exhausted",
    "contaminated",
]
RUN_MODES = frozenset({"fixture", "live"})

# Terminal statuses. A boot flake, a provider outage, and a spent budget are
# each their own status rather than a flavour of "failed": "failed" is reserved
# for a harness that ran and did not deliver, so it can be read as task quality.
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"
STATUS_CANCELLED = "cancelled"
STATUS_HARNESS_BOOT_FAILED = "harness_boot_failed"
STATUS_PROVIDER_UNAVAILABLE = "provider_unavailable"
STATUS_BUDGET_EXHAUSTED = "budget_exhausted"

# Shared discovery-gate vocabulary; live_harness re-exports these categories.
CATEGORY_AUTH_FAILED = "harness_auth_failed"
CATEGORY_SPAWN_ERROR = "harness_spawn_error"
PROVIDER_AVAILABILITY_STATUSES = frozenset(
    {STATUS_SUCCEEDED, STATUS_FAILED, STATUS_TIMEOUT, STATUS_HARNESS_BOOT_FAILED}
)
PROVIDER_AVAILABILITY_EXCLUDED_CATEGORIES = frozenset({CATEGORY_AUTH_FAILED, CATEGORY_SPAWN_ERROR})


def provider_availability_eligible(status: str, category: str | None = None) -> bool:
    return (
        status in PROVIDER_AVAILABILITY_STATUSES
        and category not in PROVIDER_AVAILABILITY_EXCLUDED_CATEGORIES
    )


TERMINAL_UNSUPPORTED_NATIVE_SEARCH = "native_search_unsupported"
TERMINAL_TOKEN_USAGE_UNKNOWN = "token_usage_unknown"
TERMINAL_HARNESS_BOOT_FAILURE = "harness_boot_failure"
TERMINAL_TIMEOUT = "timeout"
TERMINAL_CANCELLED = "cancelled"
TERMINAL_COMPLETED = "completed"
TERMINAL_PROVIDER_UNAVAILABLE = "provider_unavailable"
TERMINAL_BUDGET_EXHAUSTED = "budget_exhausted"

SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|authorization|bearer|cookie|oauth|token|secret|password|credential)",
    re.IGNORECASE,
)
BEARER_RE = re.compile(r"bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
# Credential SHAPES for the final evidence check. Prose that merely uses the word
# (a ring bearer, bearer bonds) must pass; an unredacted Authorization header,
# a bearer followed by a token-shaped value, or a Cookie header with a
# name=value pair must not.
AUTHORIZATION_HEADER_VALUE_RE = re.compile(
    r"(?i)\b(?:proxy-)?authorization\s*:\s*(?:bearer|basic|token)\s+(?!<redacted)\S+"
)
BEARER_TOKEN_SHAPE_RE = re.compile(
    r"(?i)\bbearer\s+(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{20,}"
)
COOKIE_HEADER_VALUE_RE = re.compile(r"(?i)\b(?:set-)?cookie\s*:\s*[^\s=;:]+=")
# A run id names one directory under the output root: one path component, no
# traversal, no absolute path.
SAFE_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
URL_RE = re.compile(r"https://[^\s)>\]\"'\\]+")


@dataclass(frozen=True)
class ProviderExposure:
    """Provider adapter surface made visible to a harness run."""

    provider_id: str
    tool_name: str
    fixture_sources: tuple[dict[str, Any], ...] = ()
    fixture_provider_calls: tuple[dict[str, Any], ...] = ()
    mcp_server_name: str | None = None
    mcp_server_config: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.provider_id not in PROVIDERS or self.provider_id in {
            "native",
            "fixture",
            "floor",
            "ceiling",
            "no-search",
        }:
            raise SchemaError(
                "external provider must be one of "
                "exa/parallel-web/firecrawl/brave/tavily/perplexity"
            )
        if not self.tool_name.strip():
            raise SchemaError("external provider tool_name must be non-empty")


@dataclass(frozen=True)
class HarnessRunConfig:
    harness_id: HarnessId
    provider_id: str
    task_id: str
    model_profile: str = "default"
    suite_id: str = "lighthouse"
    mode: Literal["fixture", "live"] = "fixture"
    native_search_available: bool = True
    external_provider: ProviderExposure | None = None
    fixture_outcome: str = "success"
    usage: dict[str, int] | None = field(
        default_factory=lambda: {"input": 1180, "cached_input": 0, "output": 210, "reasoning": 0}
    )
    prompt_text: str | None = None
    task_source: str = "legacy"
    run_id_override: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    harness_auth: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime(2026, 9, 16, 18, 20, tzinfo=UTC))
    # Live-mode knobs; fixture mode ignores them. Unset limits resolve from the
    # task manifest budgets (see sew.live_harness.resolve_live_limits).
    model_id: str | None = None
    # Where model_id came from: "explicit", "codex_config:top_level" or
    # "codex_config:profiles.<name>". Recorded in spawn metadata.
    model_id_origin: str | None = None
    binary: str | None = None
    harness_args: tuple[str, ...] = ()
    # GAP alone enables editing and shell tools; production keeps its old surface.
    workspace_profile: bool = False
    wheelhouse: Path | None = None
    gap_module_root: Path | None = None
    timeout_seconds: float | None = None
    boot_timeout_seconds: float | None = None
    max_total_tokens: int | None = None
    # Search calls (provider MCP or native web tool) the cell may make. Zero is
    # a real budget: the first search call exhausts it.
    max_provider_calls: int | None = None

    def __post_init__(self) -> None:
        if type(self.workspace_profile) is not bool:
            raise SchemaError("workspace_profile must be a boolean")
        if self.workspace_profile and self.mode != "live":
            raise SchemaError("GAP workspace profile requires live mode")
        if self.provider_id in {"no-search", "floor", "ceiling"}:
            if self.external_provider is not None or self.native_search_available:
                raise SchemaError(
                    "no-search baseline and GAP reference arms must disable native search "
                    "and expose no external provider"
                )
        elif self.provider_id == "native":
            if self.external_provider is not None:
                raise SchemaError("native-search baseline cannot expose an external provider")
        else:
            if self.external_provider is None:
                raise SchemaError("external-provider mode requires exactly one provider adapter")
            if self.external_provider.provider_id != self.provider_id:
                raise SchemaError("provider_id must match the exposed provider adapter")
        if self.provider_id in {"floor", "ceiling"}:
            if self.mode != "live":
                raise SchemaError("GAP reference arms require live mode")
            from .gap.reference import resolve_gap_task

            resolve_gap_task(self)
        if self.mode not in RUN_MODES:
            raise SchemaError(f"mode must be one of {sorted(RUN_MODES)}, got {self.mode!r}")
        for name in ("timeout_seconds", "boot_timeout_seconds", "max_total_tokens"):
            value = getattr(self, name)
            # NaN compares false against everything and inf never elapses, so
            # either one would silently disable the limit.
            if value is not None and (
                isinstance(value, bool) or not math.isfinite(value) or value <= 0
            ):
                raise SchemaError(f"{name} must be a finite positive number when set")
        calls = self.max_provider_calls
        if calls is not None and (
            isinstance(calls, bool) or not isinstance(calls, int) or calls < 0
        ):
            raise SchemaError("max_provider_calls must be a non-negative integer when set")
        if self.run_id_override is not None and (
            not SAFE_RUN_ID_RE.fullmatch(self.run_id_override) or ".." in self.run_id_override
        ):
            raise SchemaError(
                "run_id_override must be a single safe path component, "
                f"got {self.run_id_override!r}"
            )


@dataclass(frozen=True)
class TerminalStatus:
    status: RunStatus
    category: str


@dataclass(frozen=True)
class HarnessRunResult:
    run_id: str
    status: RunStatus
    failure_category: str | None
    bundle_dir: Path
    evidence_bundle_ref: str
    token_accounting_source: str


def fixture_provider_exposure(provider_id: str = "exa") -> ProviderExposure:
    """Return a deterministic provider exposure that mimics the SEW-02 contract."""

    return ProviderExposure(
        provider_id=provider_id,
        tool_name=f"sew_{provider_id.replace('-', '_')}_search",
        mcp_server_name={"parallel-web": "parallel"}.get(provider_id, provider_id),
        mcp_server_config={"command": "/usr/bin/true"},
        fixture_sources=(
            {
                "source_id": "src-fixture-release-note",
                "url": "https://fixture.example/releases/current",
                "title": "Fixture Product Release Notes",
                "snippet": (
                    "Fixture Product current release: Release 3.2. "
                    "Published for benchmark fixtures only."
                ),
                "published_at": "2026-09-12T10:00:00Z",
                "modified_at": "2026-09-12T10:00:00Z",
                "content_length": 88,
            },
        ),
        fixture_provider_calls=(
            {
                "operation": "search",
                "request": {"query": "fixture product current release", "redacted": True},
                "response": {"result_count": 1, "redacted": True},
            },
        ),
    )


def classify_fixture_outcome(
    *,
    outcome: str,
    native_search_available: bool,
    provider_id: str,
    usage: dict[str, int] | None,
) -> TerminalStatus:
    if provider_id == "native" and not native_search_available:
        return TerminalStatus("unsupported", TERMINAL_UNSUPPORTED_NATIVE_SEARCH)
    if outcome == "boot_failure":
        return TerminalStatus(STATUS_HARNESS_BOOT_FAILED, TERMINAL_HARNESS_BOOT_FAILURE)
    if outcome == "provider_unavailable":
        return TerminalStatus(STATUS_PROVIDER_UNAVAILABLE, TERMINAL_PROVIDER_UNAVAILABLE)
    if outcome == "budget_exhausted":
        return TerminalStatus(STATUS_BUDGET_EXHAUSTED, TERMINAL_BUDGET_EXHAUSTED)
    if outcome == "timeout":
        return TerminalStatus("timeout", TERMINAL_TIMEOUT)
    if outcome == "cancelled":
        return TerminalStatus("cancelled", TERMINAL_CANCELLED)
    if usage is None:
        return TerminalStatus("succeeded", TERMINAL_TOKEN_USAGE_UNKNOWN)
    return TerminalStatus("succeeded", TERMINAL_COMPLETED)


def run_harness(config: HarnessRunConfig, output_root: Path) -> HarnessRunResult:
    """Run one harness cell in the configured mode and write its evidence bundle."""

    if config.mode == "live":
        from .live_harness import run_live_harness

        return run_live_harness(config, output_root)
    return run_fixture_harness(config, output_root)


def run_fixture_harness(config: HarnessRunConfig, output_root: Path) -> HarnessRunResult:
    """Run a Codex/Claude Code fixture and write a SEW-01 evidence bundle."""

    if config.mode != "fixture":
        raise SchemaError("run_fixture_harness requires mode='fixture'; use run_harness")
    terminal = classify_fixture_outcome(
        outcome=config.fixture_outcome,
        native_search_available=config.native_search_available,
        provider_id=config.provider_id,
        usage=config.usage,
    )
    run_id = _run_id(config)
    run_dir = output_root / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)
    for rel in ("artifacts", "evidence", "evaluations", "metrics", "provider-calls", "sources"):
        (run_dir / rel).mkdir(parents=True, exist_ok=True)

    started_at = config.started_at
    ended_at = started_at + timedelta(seconds=8)
    prompt = config.prompt_text or _load_task_prompt(config.task_id)
    final_answer = _final_answer_for(terminal, config)
    transcript = _transcript_for(config, final_answer)
    citations = parse_citations("\n".join(_string_values((transcript, final_answer))))
    sources = _sources_for(config, citations, ended_at)
    provider_calls = _provider_calls_for(config, run_id, sources, started_at, ended_at)
    usage_rows = [config.usage] if config.usage is not None else []

    _write_json(run_dir / "artifacts" / "final-answer.json", final_answer)
    _write_text(run_dir / "artifacts" / "prompt.md", prompt)
    _write_json(run_dir / "artifacts" / "transcript.json", transcript)
    _write_json(run_dir / "artifacts" / "spawn-metadata.json", spawn_metadata(config))

    source_refs = []
    for source in sources:
        ref = f"sources/{source['source_id']}.json"
        _write_json(run_dir / ref, source)
        source_refs.append(ref)

    provider_call_refs = []
    for call in provider_calls:
        ref = f"provider-calls/{call['call_id']}.json"
        _write_json(run_dir / ref, call)
        provider_call_refs.append(ref)

    run_record = {
        "schema_version": 1,
        "run_id": run_id,
        "suite_id": config.suite_id,
        "task_id": config.task_id,
        "provider_id": config.provider_id,
        "harness_id": config.harness_id,
        "model_profile": config.model_profile,
        "mode": config.mode,
        "status": terminal.status,
        "started_at": _iso(started_at),
        "ended_at": _iso(ended_at),
        "metrics_ref": "metrics/metrics.json",
        "evaluation_ref": "evaluations/evaluation.json",
        "evidence_bundle_ref": "evidence/bundle.yaml",
        "provider_call_refs": provider_call_refs,
    }
    if terminal.category != TERMINAL_COMPLETED:
        run_record["failure_category"] = terminal.category
    _write_json(run_dir / "run.json", run_record)

    evaluation = {
        "schema_version": 1,
        "run_id": run_id,
        "task_id": config.task_id,
        "outcome": "pass" if terminal.status == "succeeded" else "not_applicable",
        "dimensions": {
            "completed": terminal.status == "succeeded",
            "correct": terminal.status == "succeeded",
            "grounded": bool(citations),
            "fresh": terminal.status == "succeeded",
            "schema_valid": True,
            "safe": True,
        },
        "failure_reasons": [] if terminal.status == "succeeded" else [terminal.category],
        "judge": {"kind": "fixture", "validator": "fixture_harness_driver"},
        "evidence_refs": source_refs,
    }
    _write_json(run_dir / "evaluations" / "evaluation.json", evaluation)
    metrics = normalize_run_metrics(
        run_record=run_record,
        provider_calls=provider_calls,
        sources=sources,
        evaluation_record=evaluation,
        harness_usage_rows=usage_rows,
        transcript=transcript,
    )
    _write_json(run_dir / "metrics" / "metrics.json", metrics)

    artifacts = []
    for rel, media_type, redaction in (
        ("artifacts/final-answer.json", "application/json", "sanitized"),
        ("artifacts/prompt.md", "text/markdown", "sanitized"),
        ("artifacts/transcript.json", "application/json", "sanitized"),
        ("artifacts/spawn-metadata.json", "application/json", "secrets-redacted"),
    ):
        artifacts.append(
            {
                "path": rel,
                "media_type": media_type,
                "size_bytes": (run_dir / rel).stat().st_size,
                "redaction": redaction,
            }
        )
    bundle = {
        "schema_version": 1,
        "bundle_id": f"{run_id}-bundle",
        "run_id": run_id,
        "redaction": {
            "raw_transcripts_included": False,
            "credentials_included": False,
            "cookies_included": False,
            "unrestricted_page_archives_included": False,
        },
        "artifacts": artifacts,
        "records": {
            "run": "run.json",
            "metrics": "metrics/metrics.json",
            "evaluation": "evaluations/evaluation.json",
            "provider_calls": provider_call_refs,
            "normalized_sources": source_refs,
        },
    }
    _write_yaml(run_dir / "evidence" / "bundle.yaml", bundle)

    validate_fixture_run(run_dir)
    assert_no_secret_material(run_dir)
    return HarnessRunResult(
        run_id=run_id,
        status=terminal.status,
        failure_category=run_record.get("failure_category"),
        bundle_dir=run_dir,
        evidence_bundle_ref="evidence/bundle.yaml",
        token_accounting_source=metrics["token_usage"]["accounting_source"],
    )


def run_acceptance_fixture_matrix(
    output_root: Path, *, task_id: str = "current-fact-lookup-v1", provider_id: str = "exa"
) -> list[HarnessRunResult]:
    results = []
    for harness_id in ("codex", "claude-code"):
        results.append(
            run_fixture_harness(
                HarnessRunConfig(harness_id=harness_id, provider_id="native", task_id=task_id),
                output_root,
            )
        )
        results.append(
            run_fixture_harness(
                HarnessRunConfig(
                    harness_id=harness_id,
                    provider_id=provider_id,
                    task_id=task_id,
                    external_provider=fixture_provider_exposure(provider_id),
                ),
                output_root,
            )
        )
    return results


def parse_citations(text: str) -> list[str]:
    seen = set()
    urls = []
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(".,")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def spawn_metadata(config: HarnessRunConfig) -> dict[str, Any]:
    if config.provider_id == "native":
        provider = {"mode": "native", "native_search_available": config.native_search_available}
    else:
        provider = {
            "mode": "external",
            "provider_id": config.provider_id,
            "tool_name": config.external_provider.tool_name if config.external_provider else None,
        }
    return {
        "harness_id": config.harness_id,
        "model_profile": config.model_profile,
        "argv": _redact(["fixture-harness", config.harness_id, "--provider", config.provider_id]),
        "env": _redact(config.env),
        "provider_exposure": provider,
    }


def assert_no_secret_material(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        # Binary artifacts still need the ASCII credential checks. Replace
        # undecodable bytes for inspection without changing the artifact.
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in (
            AUTHORIZATION_HEADER_VALUE_RE,
            BEARER_TOKEN_SHAPE_RE,
            COOKIE_HEADER_VALUE_RE,
        ):
            if pattern.search(text):
                raise SchemaError(f"secret-like material was written to evidence: {path}")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("<redacted>" if SECRET_KEY_RE.search(key) else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return BEARER_RE.sub("<redacted>", value)
    return value


def _string_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        values = []
        for item in value.values():
            values.extend(_string_values(item))
        return values
    if isinstance(value, list | tuple):
        values = []
        for item in value:
            values.extend(_string_values(item))
        return values
    if isinstance(value, str):
        return [value]
    return []


def _load_task_prompt(task_id: str) -> str:
    prompt_path = module_root() / "tasks" / task_id / "prompt.md"
    return prompt_path.read_text(encoding="utf-8")


def _run_id(config: HarnessRunConfig) -> str:
    if config.run_id_override:
        return config.run_id_override
    provider = config.provider_id.replace("-", "_")
    return f"fixture-{config.harness_id}-{provider}-{config.task_id}-20260916T182000Z"


def _final_answer_for(terminal: TerminalStatus, config: HarnessRunConfig) -> dict[str, Any]:
    if terminal.status != "succeeded":
        return {"answer": None, "citation_urls": [], "as_of_date": "2026-09-16"}
    return {
        "answer": "Release 3.2",
        "citation_urls": ["https://fixture.example/releases/current"],
        "as_of_date": "2026-09-16",
        "harness": config.harness_id,
        "provider": config.provider_id,
    }


def _transcript_for(config: HarnessRunConfig, final_answer: dict[str, Any]) -> list[dict[str, Any]]:
    started = config.started_at
    events = [
        {
            "event": "harness_ready",
            "timestamp": _iso(started + timedelta(milliseconds=700)),
            "role": "system",
        },
        {
            "event": "prompt_sent",
            "timestamp": _iso(started + timedelta(seconds=1)),
            "role": "system",
        },
        {
            "role": "user",
            "content": config.prompt_text or _load_task_prompt(config.task_id),
        },
    ]
    if config.provider_id != "native":
        events.append(
            {
                "role": "tool",
                "name": config.external_provider.tool_name
                if config.external_provider
                else "unknown",
                "content": "Search returned https://fixture.example/releases/current",
            }
        )
    else:
        events.append(
            {
                "role": "assistant",
                "content": "Native search observed https://fixture.example/releases/current",
            }
        )
    events.append(
        {
            "event": "first_output_token",
            "timestamp": _iso(started + timedelta(seconds=6)),
            "role": "assistant",
            "content": "",
        }
    )
    events.append(
        {
            "event": "final_answer",
            "timestamp": _iso(started + timedelta(seconds=7)),
            "role": "assistant",
            "content": json.dumps(final_answer, sort_keys=True),
        }
    )
    if config.usage is not None:
        events.append({"role": "telemetry", "usage": config.usage})
    return events


def _sources_for(
    config: HarnessRunConfig, citations: list[str], retrieved_at: datetime
) -> list[dict[str, Any]]:
    provider_id = config.provider_id
    if provider_id != "native" and config.external_provider is not None:
        source_templates = config.external_provider.fixture_sources
    else:
        source_templates = (
            {
                "source_id": "src-native-release-note",
                "url": "https://fixture.example/releases/current",
                "title": "Fixture Product Release Notes",
                "snippet": "Native search fixture citation for Release 3.2.",
                "published_at": "2026-09-12T10:00:00Z",
                "modified_at": "2026-09-12T10:00:00Z",
                "content_length": 47,
            },
        )
    sources = []
    for template in source_templates:
        url = template["url"]
        snippet = template["snippet"]
        sources.append(
            {
                "schema_version": 1,
                "source_id": f"{template['source_id']}-{config.harness_id}",
                "provider_id": provider_id,
                "url": url,
                "title": template["title"],
                "snippet": snippet,
                "published_at": template.get("published_at"),
                "modified_at": template.get("modified_at"),
                "retrieved_at": _iso(retrieved_at),
                "content_hash": "sha256:" + hashlib.sha256(snippet.encode("utf-8")).hexdigest(),
                "content_length": int(template["content_length"]),
                "redaction": {"state": "sanitized_excerpt", "raw_content_included": False},
            }
        )
    return sources


def _provider_calls_for(
    config: HarnessRunConfig,
    run_id: str,
    sources: list[dict[str, Any]],
    started_at: datetime,
    ended_at: datetime,
) -> list[dict[str, Any]]:
    if config.provider_id == "native":
        return []
    calls = []
    for index, template in enumerate(
        config.external_provider.fixture_provider_calls or (), start=1
    ):
        calls.append(
            {
                "schema_version": 1,
                "call_id": f"{run_id}-provider-{index}",
                "run_id": run_id,
                "provider_id": config.provider_id,
                "operation": template["operation"],
                "status": "ok",
                "started_at": _iso(started_at + timedelta(seconds=1)),
                "ended_at": _iso(ended_at - timedelta(seconds=6)),
                "request": _redact(template["request"]),
                "response": _redact(template["response"]),
                "normalized_source_refs": [
                    f"sources/{source['source_id']}.json" for source in sources
                ],
                "retry_count": 0,
            }
        )
    return calls


def _provider_call_metrics(provider_calls: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {"total": len(provider_calls)}
    for call in provider_calls:
        status = call["status"]
        counts[status] = counts.get(status, 0) + 1
    if provider_calls and "ok" not in counts:
        counts["ok"] = 0
    if provider_calls and "failed" not in counts:
        counts["failed"] = 0
    return counts


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def _write_yaml(path: Path, value: Any) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
