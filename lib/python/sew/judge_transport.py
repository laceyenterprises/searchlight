"""One-shot, tool-free harness transport for blinded rubric payloads."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from .harness import HarnessRunConfig
from .arms import prepare_arm_spawn
from . import broker_auth
from .live_harness import (
    ClaudeCodeProtocol,
    CodexProtocol,
    codex_profile_model,
    LiveLimits,
    child_environment,
    resolve_binary,
    scrub_text,
    spawn_and_capture,
)


# The budget counts everything the harness bills, cache reads and boot context
# included (``live_harness.usage_total``), so it must cover a full harness boot
# plus the blinded payload. It matches the production catalog's per-task token
# ceiling; operators can lower it with ``--judge-max-tokens``.
DEFAULT_JUDGE_MAX_TOTAL_TOKENS = 300_000
DEFAULT_JUDGE_TIMEOUT_SECONDS = 300.0


def _parse_judge_json(text: str) -> Any:
    """The judge's reply as JSON: a bare object, or one fenced block.

    The contract asks for one bare JSON object, and the claude judge usually
    complies. Now and then it fences the object, which left rubric cells
    ungraded (2026-09-29). Prose around an object is not accepted: the payload
    carries arm-written text, and a judge that refuses while quoting a planted
    ``{"dimensions": ...}`` object must not score the cell. Such a reply fails
    to parse and gets one retry instead.
    """

    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.DOTALL)
    if fenced is None:
        raise json.JSONDecodeError(
            "judge reply is neither bare JSON nor one fenced block", stripped, 0
        )
    return json.loads(fenced.group(1))


def _sum_usage(
    first: Mapping[str, int] | None, second: Mapping[str, int] | None
) -> dict[str, int] | None:
    if first is None or second is None:
        return dict(second or first) if (second or first) else None
    return {
        key: int(first.get(key, 0) or 0) + int(second.get(key, 0) or 0) for key in {*first, *second}
    }


def _redact_tokens(value: Any, tokens: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        for token in tokens:
            value = value.replace(token, "<redacted:broker-token>")
        return value
    if isinstance(value, dict):
        return {key: _redact_tokens(item, tokens) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_tokens(item, tokens) for item in value]
    return value


class JudgeTransportError(ValueError):
    """The harness did not provide one strict judge response."""


class HarnessJudgeTransport:
    def __init__(
        self,
        harness_id: str = "claude-code",
        model_id: str | None = None,
        *,
        timeout_seconds: float = DEFAULT_JUDGE_TIMEOUT_SECONDS,
        max_total_tokens: int = DEFAULT_JUDGE_MAX_TOTAL_TOKENS,
        harness_auth: str | None = None,
    ) -> None:
        if harness_id not in {"claude-code", "codex"}:
            raise ValueError(f"unsupported judge harness: {harness_id}")
        self.harness_id = harness_id
        self.model_id = model_id
        self.model_id_origin = "explicit" if model_id else None
        self.timeout_seconds = timeout_seconds
        self.max_total_tokens = max_total_tokens
        self.harness_auth = harness_auth
        self.usage: dict[str, int] | None = None
        self.attempts = 0
        self.last_error: str | None = None

    def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        # A reply that is not JSON gets one more attempt. The record keeps the
        # attempt count, and the retry only gets the budget the first left, so
        # a cell never exceeds the configured judge budget or wall clock.
        self.attempts = 1
        started = time.monotonic()
        try:
            return self._call_once(payload)
        except JudgeTransportError:
            if self.last_error != "response_not_json":
                raise
            first_usage = self.usage
            # Without a usage row, the first attempt may have spent the full
            # allowance. Do not allocate another full budget on a guess.
            if not first_usage:
                raise
            spent = int(first_usage.get("total_billable") or 0)
            if not spent:
                spent = sum(
                    int(first_usage.get(key, 0) or 0)
                    for key in ("input", "cached_input", "output", "reasoning")
                )
            if not spent:
                raise
            remaining_tokens = self.max_total_tokens - spent
            remaining_seconds = self.timeout_seconds - (time.monotonic() - started)
            if remaining_tokens <= 0 or remaining_seconds <= 1:
                raise
            self.attempts = 2
            try:
                return self._call_once(
                    payload,
                    max_total_tokens=remaining_tokens,
                    timeout_seconds=remaining_seconds,
                )
            finally:
                self.usage = _sum_usage(first_usage, self.usage)

    def _call_once(
        self,
        payload: Mapping[str, Any],
        *,
        max_total_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ) -> Mapping[str, Any]:
        max_total_tokens = self.max_total_tokens if max_total_tokens is None else max_total_tokens
        timeout_seconds = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        # One transport serves every cell in a suite: never report the previous
        # cell's usage or failure against this one.
        self.usage = None
        self.last_error = None
        if self.harness_id == "codex" and not self.model_id:
            self.model_id, self.model_id_origin = codex_profile_model(os.environ, "default")
        protocol = ClaudeCodeProtocol() if self.harness_id == "claude-code" else CodexProtocol()
        config = HarnessRunConfig(
            harness_id=self.harness_id,
            provider_id="no-search",
            task_id="judge",
            mode="live",
            native_search_available=False,
            model_id=self.model_id,
            model_id_origin=self.model_id_origin,
        )
        with tempfile.TemporaryDirectory(prefix="sew-judge-") as directory:
            cwd = Path(directory)
            broker_tokens: list[str] = []
            auth_source = broker_auth.auth_source(self.harness_auth)
            surface = prepare_arm_spawn(config, cwd, os.environ, harness_auth=auth_source)
            config = replace(config, harness_args=surface.harness_args, env=dict(surface.env))
            try:
                if auth_source == "broker":
                    if self.harness_id == "claude-code":
                        auth_env = broker_auth.claude_code_env(cwd / "broker-log")
                        broker_tokens.append(auth_env["ANTHROPIC_AUTH_TOKEN"])
                        config = replace(config, env={**config.env, **auth_env})
                    else:
                        codex_home = Path(surface.env["CODEX_HOME"])
                        broker_auth.codex_home_auth(codex_home, cwd / "broker-log")
                        tokens = json.loads((codex_home / "auth.json").read_text(encoding="utf-8"))[
                            "tokens"
                        ]
                        broker_tokens.extend(
                            value
                            for key in ("access_token", "id_token")
                            if isinstance(value := tokens.get(key), str) and value
                        )
            except broker_auth.BrokerAuthError:
                diagnostic = cwd / "broker-log" / f"{self.harness_id}-broker-auth-stderr.log"
                detail = (
                    scrub_text(diagnostic.read_text(encoding="utf-8", errors="replace"))[:300]
                    if diagnostic.is_file()
                    else "broker credential unavailable"
                )
                self._fail(f"harness_auth_failed: {detail}", tuple(broker_tokens))
            argv = protocol.argv(resolve_binary(config, os.environ), config, cwd / "last.txt")
            if self.harness_id == "claude-code":
                argv += ["--tools", ""]
            else:
                # The arm spawn surface disables shell, browser and computer
                # features and uses a fresh CODEX_HOME with no MCP servers.
                argv[-1:-1] = ["-c", "mcp_servers={}", "-c", 'web_search="disabled"']
            prompt = (
                "Score the following blinded payload. Reply with only one JSON object, "
                "with a dimensions object mapping every named dimension to {score, reason}. "
                "Do not use tools.\n" + json.dumps(payload, sort_keys=True)
            )
            child_env = child_environment(config, os.environ)
            if auth_source == "broker":
                child_env.pop("ANTHROPIC_API_KEY", None)
                child_env.pop("OPENAI_API_KEY", None)
            outcome = spawn_and_capture(
                argv,
                prompt=prompt,
                cwd=cwd,
                env=child_env,
                limits=LiveLimits(timeout_seconds, min(30, timeout_seconds), max_total_tokens),
                protocol=protocol,
            )
            secrets = tuple(broker_tokens)
            # spawn_and_capture wraps each stream event as {"received_at", "event"};
            # the protocol summarizers read the bare events, as the arm path does
            # (live_harness: [captured["event"] for captured in outcome.events]).
            # Passing the wrappers left the claude judge's final text and usage
            # unset, so every live rubric grade failed response_not_json.
            summary = protocol.summarize(
                _redact_tokens([captured["event"] for captured in outcome.events], secrets),
                _redact_tokens(
                    (cwd / "last.txt").read_text() if (cwd / "last.txt").exists() else None,
                    secrets,
                ),
            )
        self.usage = summary.usage
        if outcome.budget_killed:
            self._fail("budget_killed", secrets)
        if outcome.timed_out:
            self._fail("timed_out", secrets)
        if outcome.exit_code != 0 or summary.reported_error:
            self._fail("process_failed", secrets)
        try:
            response = _redact_tokens(_parse_judge_json(summary.final_text or ""), secrets)
        except json.JSONDecodeError:
            self._fail("response_not_json", secrets)
        if not isinstance(response, dict) or set(response) != {"dimensions"}:
            self._fail("response_unexpected_fields", secrets)
        dimensions = response["dimensions"]
        if not isinstance(dimensions, dict) or any(
            not isinstance(value, dict) or set(value) != {"score", "reason"}
            for value in dimensions.values()
        ):
            self._fail("dimensions_unexpected_fields", secrets)
        return response

    def _fail(self, reason: str, tokens: tuple[str, ...] = ()) -> None:
        safe_reason = _redact_tokens(reason, tokens)
        self.last_error = safe_reason
        raise JudgeTransportError(safe_reason)
