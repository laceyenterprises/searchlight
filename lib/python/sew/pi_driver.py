"""Pi OSS harness driver for SEW fixture and live-smoke runs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from . import harnesses
from .catalog import module_root
from .schema import (
    PROVIDERS,
    TOKEN_ACCOUNTING_SOURCES,
    TOOL_CALLING_MODES,
    SchemaError,
    load_suite_manifest,
    load_task_manifest,
    validate_fixture_run,
)

LIVE_ENV = "SEW_PI_LIVE"
PI_BIN_ENV = harnesses.get("pi").bin_env
PI_DEFAULT_BIN = harnesses.get("pi").default_bin
LIVE_PROVIDER_ENV = {
    "exa": "EXA_API_KEY",
    "parallel-web": "PARALLEL_WEB_API_KEY",
    "firecrawl": "FIRECRAWL_API_KEY",
    "brave": "BRAVE_SEARCH_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
}


@dataclass(frozen=True)
class PiModelProfile:
    profile_id: str
    resolved_model: str
    tool_calling_mode: str
    context_window_tokens: int
    provider_result_compression: dict[str, Any]
    token_accounting_source: str
    provider_adapter_exposure: dict[str, Any]

    @property
    def supports_external_tools(self) -> bool:
        return self.tool_calling_mode in {"native", "prompt_wrapped"}


@dataclass(frozen=True)
class PiRunResult:
    run_id: str
    status: str
    run_dir: Path


@dataclass(frozen=True)
class LiveSmokeResult:
    status: str
    reason: str
    run_dir: Path | None = None


class PiHarnessDriver:
    def __init__(self, profiles: Mapping[str, PiModelProfile], *, pi_bin: str = "pi") -> None:
        self._profiles = dict(profiles)
        self._pi_bin = pi_bin

    @classmethod
    def from_config(
        cls, path: Path | None = None, *, pi_bin: str | None = None
    ) -> "PiHarnessDriver":
        config_path = path or module_root() / "fixtures" / "pi-model-profiles.yaml"
        data = _load_yaml(config_path)
        profiles = load_pi_profiles(data)
        return cls(profiles, pi_bin=pi_bin or os.environ.get(PI_BIN_ENV, PI_DEFAULT_BIN))

    @property
    def profile_ids(self) -> list[str]:
        return sorted(self._profiles)

    def profile(self, profile_id: str) -> PiModelProfile:
        try:
            return self._profiles[profile_id]
        except KeyError as exc:
            raise SchemaError(f"unknown Pi model profile: {profile_id}") from exc

    def run_lighthouse_fixture(
        self,
        output_root: Path,
        *,
        profile_ids: list[str] | None = None,
        provider_id: str = "fixture",
    ) -> list[PiRunResult]:
        suite = load_suite_manifest(module_root() / "catalogs" / "lighthouse")
        selected = profile_ids or list(suite["harnesses"]["pi"]["model_profiles"])
        results: list[PiRunResult] = []
        for profile_id in selected:
            profile = self.profile(profile_id)
            for task_id in suite["tasks"]:
                task = load_task_manifest(module_root() / "tasks" / task_id)
                results.append(
                    self.run_fixture_task(
                        output_root,
                        profile=profile,
                        provider_id=provider_id,
                        task_id=task["task_id"],
                        suite_id=suite["suite_id"],
                    )
                )
        return results

    def run_fixture_task(
        self,
        output_root: Path,
        *,
        profile: PiModelProfile,
        provider_id: str,
        task_id: str,
        suite_id: str = "lighthouse",
        run_id: str | None = None,
    ) -> PiRunResult:
        _ensure_provider(provider_id)
        run_id = run_id or f"pi-{profile.profile_id}-{provider_id}-{task_id}-fixture"
        run_dir = output_root / run_id
        supported = _provider_supported(profile, provider_id)
        status = "succeeded" if supported else "unsupported"
        _write_fixture_bundle(
            run_dir,
            run_id=run_id,
            suite_id=suite_id,
            task_id=task_id,
            provider_id=provider_id,
            profile=profile,
            status=status,
            fixture_mode=True,
        )
        validate_fixture_run(run_dir)
        return PiRunResult(run_id=run_id, status=status, run_dir=run_dir)

    def run_live_smoke(
        self,
        output_root: Path,
        *,
        profile_id: str,
        provider_id: str,
        task_id: str = "current-fact-lookup-v1",
        env: Mapping[str, str] | None = None,
    ) -> LiveSmokeResult:
        # Compatibility entry point: a help probe is not a live model run.
        # Search cells now use the registry-backed run-live-harness command.
        return LiveSmokeResult(
            "skipped", "use sew run-live-harness --harness pi --model litellm/<route> "
            "with an explicit provider MCP config"
        )


def load_pi_profiles(data: Any) -> dict[str, PiModelProfile]:
    if not isinstance(data, dict):
        raise SchemaError("Pi profile config must be an object")
    if data.get("schema_version") != 1:
        raise SchemaError("Pi profile config.schema_version must be 1")
    raw_profiles = data.get("profiles")
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise SchemaError("Pi profile config.profiles must be a non-empty object")
    profiles: dict[str, PiModelProfile] = {}
    for profile_id, raw_profile in raw_profiles.items():
        if not isinstance(profile_id, str) or not profile_id:
            raise SchemaError("Pi profile ids must be non-empty strings")
        profile = _profile_from_mapping(profile_id, raw_profile)
        profiles[profile.profile_id] = profile
    return profiles


def _profile_from_mapping(profile_id: str, raw_profile: Any) -> PiModelProfile:
    if not isinstance(raw_profile, dict):
        raise SchemaError(f"Pi profile {profile_id} must be an object")
    resolved_model = _required_str(raw_profile, "resolved_model", profile_id)
    tool_mode = _required_str(raw_profile, "tool_calling_mode", profile_id)
    if tool_mode not in TOOL_CALLING_MODES:
        raise SchemaError(f"Pi profile {profile_id}.tool_calling_mode is invalid: {tool_mode}")
    token_source = _required_str(raw_profile, "token_accounting_source", profile_id)
    if token_source not in TOKEN_ACCOUNTING_SOURCES:
        raise SchemaError(
            f"Pi profile {profile_id}.token_accounting_source is invalid: {token_source}"
        )
    context = raw_profile.get("context_window_tokens")
    if not isinstance(context, int) or isinstance(context, bool) or context <= 0:
        raise SchemaError(f"Pi profile {profile_id}.context_window_tokens must be positive")
    compression = _required_mapping(raw_profile, "provider_result_compression", profile_id)
    exposure = _required_mapping(raw_profile, "provider_adapter_exposure", profile_id)
    provider_ids = exposure.get("provider_ids")
    if not isinstance(provider_ids, list):
        raise SchemaError(
            f"Pi profile {profile_id}.provider_adapter_exposure.provider_ids must be a list"
        )
    for provider in provider_ids:
        _ensure_provider(provider)
    return PiModelProfile(
        profile_id=profile_id,
        resolved_model=resolved_model,
        tool_calling_mode=tool_mode,
        context_window_tokens=context,
        provider_result_compression=dict(compression),
        token_accounting_source=token_source,
        provider_adapter_exposure=dict(exposure),
    )


def _write_fixture_bundle(
    run_dir: Path,
    *,
    run_id: str,
    suite_id: str,
    task_id: str,
    provider_id: str,
    profile: PiModelProfile,
    status: str,
    fixture_mode: bool,
) -> None:
    for leaf in (
        "metrics",
        "evaluations",
        "evidence",
        "harness",
        "provider-calls",
        "sources",
        "artifacts",
    ):
        (run_dir / leaf).mkdir(parents=True, exist_ok=True)
    started_at = "2026-09-16T18:12:00Z"
    ended_at = "2026-09-16T18:12:07Z"
    provider_call_status = "not_applicable" if status == "unsupported" else "ok"
    provider_result_chars = 0 if status == "unsupported" else 512
    provider_calls = {"total": 1, provider_call_status: 1}
    token_usage = (
        _unsupported_token_usage(profile) if status == "unsupported" else _token_usage(profile)
    )
    evaluation_outcome = "not_applicable" if status == "unsupported" else "pass"
    run = {
        "schema_version": 1,
        "run_id": run_id,
        "suite_id": suite_id,
        "task_id": task_id,
        "provider_id": provider_id,
        "harness_id": "pi",
        "model_profile": profile.profile_id,
        "mode": "fixture" if fixture_mode else "live",
        "status": status,
        "started_at": started_at,
        "ended_at": ended_at,
        "metrics_ref": "metrics/metrics.json",
        "evaluation_ref": "evaluations/evaluation.json",
        "evidence_bundle_ref": "evidence/bundle.yaml",
        "harness_ref": "harness/pi.json",
        "provider_call_refs": ["provider-calls/provider.json"],
    }
    if status == "unsupported":
        run["failure_category"] = "tool_calling_unsupported"
    _write_json(run_dir / "run.json", run)
    _write_json(
        run_dir / "metrics" / "metrics.json",
        {
            "schema_version": 1,
            "run_id": run_id,
            "latency_ms": {
                "end_to_end": 7000,
                "provider": 0 if status == "unsupported" else 250,
                "harness": 6400,
                "evaluator": 350,
            },
            "token_usage": token_usage,
            "provider_calls": provider_calls,
            "provider_result_chars": provider_result_chars,
            "source_counts": {
                "normalized": 0 if status == "unsupported" else 1,
                "cited": 0 if status == "unsupported" else 1,
            },
            "cost": {
                "currency": "USD",
                "amount": None,
                "source": token_usage["accounting_source"],
            },
        },
    )
    _write_json(
        run_dir / "evaluations" / "evaluation.json",
        {
            "schema_version": 1,
            "run_id": run_id,
            "task_id": task_id,
            "outcome": evaluation_outcome,
            "dimensions": {
                "completed": status != "unsupported",
                "correct": status != "unsupported",
                "grounded": status != "unsupported",
                "fresh": status != "unsupported",
                "schema_valid": True,
                "safe": True,
            },
            "failure_reasons": [] if status != "unsupported" else ["tool_calling_unsupported"],
            "judge": {"kind": "fixture", "model_profile": profile.profile_id},
            "evidence_refs": [] if status == "unsupported" else ["sources/source.json"],
        },
    )
    _write_json(run_dir / "harness" / "pi.json", _harness_record(run_id, profile, fixture_mode))
    _write_json(
        run_dir / "provider-calls" / "provider.json",
        {
            "schema_version": 1,
            "call_id": f"{run_id}-provider",
            "run_id": run_id,
            "provider_id": provider_id,
            "operation": "search",
            "status": provider_call_status,
            "started_at": started_at,
            "ended_at": ended_at,
            "request": {"fixture": fixture_mode, "task_id": task_id},
            "response": {"tool_calling_mode": profile.tool_calling_mode},
            "normalized_source_refs": [] if status == "unsupported" else ["sources/source.json"],
            "retry_count": 0,
            "error_class": "tool_calling_unsupported" if status == "unsupported" else None,
        },
    )
    if status != "unsupported":
        _write_json(
            run_dir / "sources" / "source.json",
            {
                "schema_version": 1,
                "source_id": f"{run_id}-source",
                "provider_id": provider_id,
                "url": "fixture://sew/pi/lighthouse",
                "title": "SEW Pi fixture source",
                "snippet": "Sanitized fixture evidence used to exercise the Pi harness driver.",
                "retrieved_at": started_at,
                "content_hash": "sha256:pi-fixture",
                "content_length": 67,
                "redaction": {"state": "sanitized_excerpt", "raw_content_included": False},
            },
        )
    _write_json(
        run_dir / "artifacts" / "final-answer.json",
        {"answer": "fixture result", "profile_id": profile.profile_id, "status": status},
    )
    _write_yaml(
        run_dir / "evidence" / "bundle.yaml",
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
            "artifacts": [
                {
                    "path": "artifacts/final-answer.json",
                    "media_type": "application/json",
                    "size_bytes": (run_dir / "artifacts" / "final-answer.json").stat().st_size,
                    "redaction": "sanitized",
                }
            ],
            "records": {
                "run": "run.json",
                "metrics": "metrics/metrics.json",
                "evaluation": "evaluations/evaluation.json",
                "harness": "harness/pi.json",
                "provider_calls": ["provider-calls/provider.json"],
                "normalized_sources": [] if status == "unsupported" else ["sources/source.json"],
            },
        },
    )


def _harness_record(run_id: str, profile: PiModelProfile, fixture_mode: bool) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "harness_id": "pi",
        "profile_id": profile.profile_id,
        "resolved_model": profile.resolved_model,
        "tool_calling_mode": profile.tool_calling_mode,
        "context_window_tokens": profile.context_window_tokens,
        "provider_result_compression": profile.provider_result_compression,
        "token_accounting_source": profile.token_accounting_source,
        "provider_adapter_exposure": profile.provider_adapter_exposure,
        "fixture_mode": fixture_mode,
    }


def _token_usage(profile: PiModelProfile) -> dict[str, int | str | None]:
    source = profile.token_accounting_source
    if source == "measured":
        return {
            "accounting_source": source,
            "input": 1200,
            "cached_input": 0,
            "output": 180,
            "reasoning": 0,
        }
    if source == "estimated":
        return {
            "accounting_source": source,
            "input": 1500,
            "cached_input": None,
            "output": 220,
            "reasoning": None,
        }
    return {
        "accounting_source": source,
        "input": None,
        "cached_input": None,
        "output": None,
        "reasoning": None,
    }


def _unsupported_token_usage(profile: PiModelProfile) -> dict[str, int | str | None]:
    return {
        "accounting_source": profile.token_accounting_source,
        "input": 0,
        "cached_input": 0,
        "output": 0,
        "reasoning": 0,
    }


def _provider_supported(profile: PiModelProfile, provider_id: str) -> bool:
    if provider_id == "fixture":
        return True
    if provider_id == "native":
        return bool(profile.provider_adapter_exposure.get("native_search_available", False))
    return profile.supports_external_tools


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SchemaError(f"cannot load Pi profile config {path}: {exc}") from exc


def _required_str(data: Mapping[str, Any], key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise SchemaError(f"Pi profile {where}.{key} must be a non-empty string")
    return value


def _required_mapping(data: Mapping[str, Any], key: str, where: str) -> Mapping[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise SchemaError(f"Pi profile {where}.{key} must be an object")
    return value


def _ensure_provider(provider_id: str) -> None:
    if provider_id not in PROVIDERS:
        raise SchemaError(f"unknown provider_id: {provider_id}")


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_yaml(path: Path, data: Any) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


__all__ = [
    "LIVE_ENV",
    "LIVE_PROVIDER_ENV",
    "PiHarnessDriver",
    "PiModelProfile",
    "PiRunResult",
    "LiveSmokeResult",
    "load_pi_profiles",
]
