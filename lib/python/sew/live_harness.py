"""Live Claude Code and Codex harness driver (WSB-05).

This is the half of SEW-03 that really spawns a harness: prompt injection over
stdin, streamed JSONL transcript capture, a boot deadline, a wall-clock
timeout, cooperative cancel, an optional token budget, and terminal-status
classification. It writes the same SEW-01 evidence bundle as fixture mode.

The argv mirrors the fleet's existing non-interactive spawn paths rather than
inventing a third way to launch a harness:

* Claude Code: ``claude --print --output-format stream-json --verbose``
  (``modules/worker-pool/lib/adapters/claude-code.sh``).
* Codex: ``codex exec --json --ephemeral ... -`` with the prompt on stdin
  (``modules/worker-pool/lib/adapters/acpx-codex.sh``).

The child environment is an allowlist, not the caller's environment, for the
same reason the adapters run under ``env -i``: a benchmark harness spawned from
inside an agent session otherwise inherits that session's messaging socket,
worker GitHub token, and nesting markers.

Tool exposure is deliberately out of scope. ``HarnessRunConfig.harness_args``
and ``HarnessRunConfig.env`` are the seams WSB-06 fills with the arm contract.

Live mode is operator-gated: nothing spawns unless ``SEW_HARNESS_LIVE=1`` is
set in the environment the driver is given. CI never sets it.
"""

from __future__ import annotations

import json
import os
import queue
import re
import select
import signal
import subprocess
import tempfile
import threading
import time
import tomllib
from collections.abc import Callable, Mapping
from contextlib import ExitStack, closing
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .catalog import module_root
from .harness import (
    CATEGORY_AUTH_FAILED,
    CATEGORY_SPAWN_ERROR,
    STATUS_BUDGET_EXHAUSTED,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_HARNESS_BOOT_FAILED,
    STATUS_PROVIDER_UNAVAILABLE,
    STATUS_SUCCEEDED,
    STATUS_TIMEOUT,
    TERMINAL_COMPLETED,
    TERMINAL_TOKEN_USAGE_UNKNOWN,
    TERMINAL_UNSUPPORTED_NATIVE_SEARCH,
    SAFE_RUN_ID_RE,
    HarnessRunConfig,
    HarnessRunResult,
    TerminalStatus,
    _iso,
    _redact,
    _write_json,
    _write_text,
    _write_yaml,
    assert_no_secret_material,
    parse_citations,
)
from .mcp_meter import AVAILABILITY_REF, OBSERVATION_FAILURE_REF
from .mcp_meter import metered_server_config, provider_available
from .metrics import normalize_run_metrics
from .schema import SchemaError, load_task_manifest, validate_fixture_run, validate_provider_call
from .task_resolution import resolve_task
from .arms import ArmContract, audit_transcript, prepare_arm_spawn, provider_tool_calls
from . import broker_auth, harnesses

LIVE_ENV = "SEW_HARNESS_LIVE"
# Live views over the registry's live harnesses (sew.harnesses).
BIN_ENV = harnesses.field_map("bin_env", live=True)
DEFAULT_BIN = harnesses.field_map("default_bin", live=True)

DEFAULT_TIMEOUT_SECONDS = 600.0
DEFAULT_BOOT_TIMEOUT_SECONDS = 120.0
KILL_GRACE_SECONDS = 5.0
STDERR_TAIL_BYTES = 16_000
# schema.validate_evidence_bundle caps every listed artifact at 200,000 bytes.
ARTIFACT_CAP_BYTES = 190_000
# Meter snapshot written before the complete evidence bundle exists. If the
# operator interrupts a live cell mid-stream, resume can still charge the
# provider calls/tokens the child already spent.
PROVISIONAL_METERS_REF = "artifacts/provisional-meters.json"
# Raw stdout the driver will hold for one cell. The artifact cap applies only
# after the child exits, so without a streaming bound a runaway harness could
# exhaust the driver's memory before its deadline. Past this budget the cell
# stops as budget_exhausted and keeps what it already captured.
CAPTURE_BUDGET_BYTES = 32 * 1024 * 1024
# Protocol fields that identify what ran (event kinds, roles, tool names and
# ids). Elision never touches them: the WSB-06 contamination check reads them.
PROTOCOL_KEYS = frozenset(
    {
        "type",
        "subtype",
        "role",
        "name",
        "id",
        "tool_use_id",
        "server",
        "tool",
        "status",
        "event",
        "received_at",
        "timestamp",
        "model",
    }
)
# Env values a spawn record may keep. Everything else is recorded by name only:
# an arm credential under an unfamiliar key (MCP_AUTH=...) must never reach
# evidence, and a key-name deny list cannot know every such key.
ENV_VALUES_RECORDED = frozenset({"LANG", "LC_ALL", "NO_COLOR", "TZ"})

# Failure categories: the finer-grained reason beside the terminal status.
CATEGORY_NO_FIRST_OUTPUT = "harness_no_first_output"
CATEGORY_EXITED_BEFORE_READY = "harness_exited_before_ready"
CATEGORY_PROVIDER_UNAVAILABLE = "provider_unavailable"
CATEGORY_TOKEN_BUDGET = "token_budget_exceeded"
CATEGORY_PROVIDER_CALL_BUDGET = "provider_call_budget_exceeded"
CATEGORY_HARNESS_BUDGET = "harness_budget_limit"
CATEGORY_TIMEOUT = "timeout"
CATEGORY_CANCELLED = "cancelled"
CATEGORY_EXIT_NONZERO = "harness_exit_nonzero"
CATEGORY_REPORTED_ERROR = "harness_reported_error"
CATEGORY_EMPTY_ANSWER = "empty_final_answer"
CATEGORY_TRANSCRIPT_OVERFLOW = "transcript_over_artifact_cap"
CATEGORY_ANSWER_OVERFLOW = "final_answer_over_artifact_cap"
CATEGORY_CAPTURE_BUDGET = "evidence_capture_budget_exceeded"
CATEGORY_CONTAMINATED = "out_of_arm_tool_call"
CATEGORY_WORKSPACE_DIFF_REFUSED = "workspace_diff_refused"

# Environment the harness child may inherit. Everything else is dropped; the
# caller adds provider keys and the like through HarnessRunConfig.env.
ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TMPDIR",
        "LANG",
        "TERM",
        "TZ",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "NODE_EXTRA_CA_CERTS",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        # Harness auth. Values pass through to the child and are never recorded.
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CONFIG_DIR",
        "CODEX_HOME",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
    }
)
ENV_ALLOWLIST_PREFIXES = ("LC_",)

# Classification vocabulary over the harness's own error text and stderr.
# Auth is a boot failure: the harness never got to attempt the task.
AUTH_ERROR_RE = re.compile(
    r"\b401\b|unauthori[sz]ed|invalid api key|invalid x-api-key|not logged in|"
    r"please run /login|log out and sign in|access token could not be refreshed|"
    r"authentication_error|token_expired|oauth token has expired",
    re.IGNORECASE,
)
PROVIDER_ERROR_RE = re.compile(
    r"\b(429|529|502|503|504)\b|rate.?limit|overloaded|usage limit|quota|"
    r"credit balance|insufficient_quota|at capacity|service unavailable|"
    r"api error: 5\d\d|stream disconnected|connection (refused|reset)|"
    r"econnrefused|econnreset|enotfound|etimedout|network error",
    re.IGNORECASE,
)
CLAUDE_BUDGET_SUBTYPES = frozenset({"error_max_turns", "error_max_budget_usd"})

# Keys whose values are secrets regardless of content. Narrower than
# harness.SECRET_KEY_RE on purpose: that one matches "token" anywhere, which
# would redact the harness's own input_tokens/output_tokens usage counters.
TRANSCRIPT_SECRET_KEY_RE = re.compile(
    r"(^|[_-])(api[_-]?key|authorization|cookie|set[_-]cookie|secret|client[_-]secret|"
    r"password|passwd|credential|access[_-]token|refresh[_-]token|id[_-]token|"
    r"auth[_-]token|session[_-]token|private[_-]key)$",
    re.IGNORECASE,
)
AUTHORIZATION_BEARER_HEADER_RE = re.compile(r"(?i)\b((?:proxy-)?authorization\s*:\s*)bearer\s+\S+")
BEARER_PLACEHOLDER_RE = re.compile(r"(?i)\bbearer\s+<[^>\s]+>")
COOKIE_HEADER_RE = re.compile(r"(set-)?cookie:[^\r\n]*", re.IGNORECASE)


class LiveHarnessRefused(SchemaError):
    """Raised when live mode is requested without the operator gate."""


class TranscriptOverflow(SchemaError):
    """Raised when a transcript exceeds the artifact cap even fully elided."""


@dataclass(frozen=True)
class LiveLimits:
    timeout_seconds: float
    boot_timeout_seconds: float
    max_total_tokens: int | None
    max_provider_calls: int | None = None


@dataclass
class ProcessOutcome:
    """What happened to the child process, before any classification."""

    events: list[dict[str, Any]] = field(default_factory=list)
    stderr_tail: str = ""
    exit_code: int | None = None
    spawn_error: str | None = None
    ready: bool = False
    timed_out: bool = False
    boot_timed_out: bool = False
    cancelled: bool = False
    budget_killed: bool = False
    provider_budget_killed: bool = False
    capture_overflow: bool = False
    running_tokens: int = 0
    provider_calls: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
    prompt_sent_at: datetime | None = None
    ready_at: datetime | None = None
    first_output_at: datetime | None = None
    last_event_at: datetime | None = None


@dataclass(frozen=True)
class HarnessSummary:
    """Protocol-level reading of a transcript: answer, usage, and errors."""

    final_text: str | None
    usage: dict[str, int] | None
    error_messages: tuple[str, ...]
    reported_error: bool
    harness_budget_hit: bool


class HarnessProtocol:
    """Per-harness argv and stream-event vocabulary."""

    harness_id = ""

    def argv(self, binary: str, config: HarnessRunConfig, last_message_path: Path) -> list[str]:
        raise NotImplementedError

    def prompt_argv(self, binary, config, last_message_path, prompt):
        """Launch arguments for harnesses that transport prompts in argv."""
        return self.argv(binary, config, last_message_path)

    def is_ready(self, event: Mapping[str, Any]) -> bool:
        raise NotImplementedError

    def is_output(self, event: Mapping[str, Any]) -> bool:
        raise NotImplementedError

    def running_usage(self, event: Mapping[str, Any], seen: dict[str, int]) -> None:
        """Fold one event's token usage into ``seen`` for live budget checks."""

    def summarize(self, events: list[dict[str, Any]], last_message: str | None) -> HarnessSummary:
        raise NotImplementedError


class ClaudeCodeProtocol(HarnessProtocol):
    harness_id = "claude-code"

    def argv(self, binary: str, config: HarnessRunConfig, last_message_path: Path) -> list[str]:
        argv = [
            binary,
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--no-session-persistence",
        ]
        if config.model_id:
            argv += ["--model", config.model_id.removeprefix("litellm/")]
        return argv + list(config.harness_args)

    def is_ready(self, event: Mapping[str, Any]) -> bool:
        return event.get("type") == "system" and event.get("subtype") == "init"

    def is_output(self, event: Mapping[str, Any]) -> bool:
        return event.get("type") in {"assistant", "result"}

    def running_usage(self, event: Mapping[str, Any], seen: dict[str, int]) -> None:
        # stream-json repeats one API message across its content blocks; key by
        # message id so a multi-block turn is counted once.
        if event.get("type") != "assistant":
            return
        message = event.get("message")
        if not isinstance(message, Mapping) or not isinstance(message.get("usage"), Mapping):
            return
        key = str(message.get("id") or len(seen))
        seen[key] = _claude_total(message["usage"])

    def summarize(self, events: list[dict[str, Any]], last_message: str | None) -> HarnessSummary:
        result = next((e for e in reversed(events) if e.get("type") == "result"), None)
        errors: list[str] = []
        if result is None:
            return HarnessSummary(None, None, (), False, False)
        is_error = bool(result.get("is_error")) or result.get("subtype") != "success"
        text = result.get("result") if isinstance(result.get("result"), str) else None
        if is_error:
            errors.append(str(text or result.get("subtype") or "error"))
            if result.get("api_error_status") is not None:
                errors.append(f"api_error_status {result['api_error_status']}")
        usage = _claude_usage_row(result.get("usage"))
        return HarnessSummary(
            final_text=None if is_error else text,
            usage=usage,
            error_messages=tuple(errors),
            reported_error=is_error,
            harness_budget_hit=result.get("subtype") in CLAUDE_BUDGET_SUBTYPES,
        )


class CodexProtocol(HarnessProtocol):
    harness_id = "codex"

    def argv(self, binary: str, config: HarnessRunConfig, last_message_path: Path) -> list[str]:
        argv = [binary]
        if config.provider_id == "native":
            # A top-level option, not an `exec` flag: codex-cli 0.157.0 lists
            # --search in `codex --help` and not in `codex exec --help`, so it
            # must precede the subcommand.
            argv.append("--search")
        argv += [
            "exec",
            "--json",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write" if config.workspace_profile else "read-only",
            "--output-last-message",
            str(last_message_path),
        ]
        if config.model_id:
            argv += ["--model", config.model_id.removeprefix("litellm/")]
        return argv + list(config.harness_args) + ["-"]

    def is_ready(self, event: Mapping[str, Any]) -> bool:
        return event.get("type") == "thread.started"

    def is_output(self, event: Mapping[str, Any]) -> bool:
        item = event.get("item")
        return (
            event.get("type") == "item.completed"
            and isinstance(item, Mapping)
            and item.get("type") in {"agent_message", "reasoning"}
        )

    def running_usage(self, event: Mapping[str, Any], seen: dict[str, int]) -> None:
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), Mapping):
            usage = event["usage"]
            seen[str(len(seen))] = _int(usage.get("input_tokens")) + _int(
                usage.get("output_tokens")
            )

    def summarize(self, events: list[dict[str, Any]], last_message: str | None) -> HarnessSummary:
        final_text = None
        errors: list[str] = []
        turn_failed = False
        usage_rows = []
        for event in events:
            kind = event.get("type")
            item = event.get("item") if isinstance(event.get("item"), Mapping) else {}
            if kind == "item.completed" and item.get("type") == "agent_message":
                final_text = item.get("text") if isinstance(item.get("text"), str) else final_text
            elif kind == "turn.completed" and isinstance(event.get("usage"), Mapping):
                usage_rows.append(event["usage"])
            elif kind == "turn.failed":
                turn_failed = True
                error = event.get("error")
                errors.append(str(error.get("message") if isinstance(error, Mapping) else error))
            elif kind == "error":
                message = str(event.get("message") or "")
                # Transport retries are progress, not a verdict; the terminal
                # error (if any) arrives as turn.failed or a final error event.
                if not message.startswith("Reconnecting"):
                    errors.append(message)
        if final_text is None and last_message and last_message.strip():
            final_text = last_message
        return HarnessSummary(
            final_text=None if turn_failed else final_text,
            usage=_codex_usage_row(usage_rows),
            error_messages=tuple(errors),
            reported_error=turn_failed,
            harness_budget_hit=False,
        )


# {harness_id: protocol}, resolved from each live harness's registry entry.
PROTOCOLS: Mapping[str, HarnessProtocol] = harnesses.protocols()


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def codex_profile_model(
    source_env: Mapping[str, str], profile: str
) -> tuple[str | None, str | None]:
    """The model the source Codex config selects, and where that setting lives.

    Mirrors Codex's own resolution in ``$CODEX_HOME/config.toml``: top-level
    ``model``/``model_provider``, then the active profile's
    ``[profiles.<name>]`` table merged over them. The active profile is the
    SEW model profile when it is not ``default``, else Codex's own top-level
    ``profile`` key. A named profile with no table is unknown rather than
    silently the top-level model. A model is pinned only when its provider is
    OpenAI, the only provider the isolated child has.
    """

    home = Path(source_env.get("CODEX_HOME") or Path(source_env.get("HOME", "")) / ".codex")
    try:
        settings = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    model = _text(settings.get("model"))
    provider = _text(settings.get("model_provider"))
    origin = "codex_config:top_level" if model else None
    active = profile if profile != "default" else settings.get("profile")
    if active is not None:
        if not isinstance(active, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", active):
            return None, None
        profiles = settings.get("profiles")
        table = profiles.get(active) if isinstance(profiles, dict) else None
        if not isinstance(table, dict):
            return None, None
        if _text(table.get("model")):
            model = _text(table.get("model"))
            origin = f"codex_config:profiles.{active}"
        if _text(table.get("model_provider")):
            provider = _text(table.get("model_provider"))
    if model is None or provider not in (None, "openai"):
        return None, None
    return model, origin


def _scrub_canary_credentials(value, environment):
    """Redact string leaves and keys without altering JSON types or escaping."""
    secrets = sorted(
        {v for k, v in environment.items() if v and TRANSCRIPT_SECRET_KEY_RE.search(k)},
        key=len,
        reverse=True,
    )

    def scrub(item):
        if isinstance(item, str):
            for secret in secrets:
                item = item.replace(secret, "<redacted:canary-credential>")
            return item
        if isinstance(item, dict):
            return {scrub(k): scrub(v) for k, v in item.items()}
        if isinstance(item, list):
            return [scrub(v) for v in item]
        return item

    return scrub(value)


def live_enabled(environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return source.get(LIVE_ENV) == "1"


def resolve_live_limits(config: HarnessRunConfig) -> LiveLimits:
    """Explicit config wins, then the task manifest wall clock, then defaults.

    The token and provider-call caps are enforced only when the caller passes
    them. The driver stays policy-free; the suite runner's live executor
    (``sew.runner.LiveCellExecutor``) is what passes a task's manifest budgets,
    so a one-off ``run-live-harness`` spawn is not held to a retrieval-sized
    budget it was never given.
    """

    budgets: Mapping[str, Any] = {}
    if config.task_source == "production":
        budgets = resolve_task(config.task_id).budgets
    else:
        task_dir = module_root() / "tasks" / config.task_id
        if (task_dir / "task.yaml").is_file():
            budgets = load_task_manifest(task_dir).get("budgets") or {}
    timeout = config.timeout_seconds or float(
        budgets.get("wall_clock_seconds") or DEFAULT_TIMEOUT_SECONDS
    )
    if config.task_source == "production":
        timeout = min(timeout, budgets["wall_clock_seconds"])
    boot = config.boot_timeout_seconds or DEFAULT_BOOT_TIMEOUT_SECONDS
    production = config.task_source == "production"
    return LiveLimits(
        timeout_seconds=float(timeout),
        boot_timeout_seconds=float(min(boot, timeout)),
        max_total_tokens=(
            min(config.max_total_tokens, budgets["max_total_tokens"])
            if config.max_total_tokens is not None and production
            else budgets["max_total_tokens"]
            if production
            else config.max_total_tokens
        ),
        max_provider_calls=(
            min(config.max_provider_calls, budgets["max_provider_calls"])
            if config.max_provider_calls is not None and production
            else budgets["max_provider_calls"]
            if production
            else config.max_provider_calls
        ),
    )


def resolve_binary(config: HarnessRunConfig, environ: Mapping[str, str]) -> str:
    return (
        config.binary or environ.get(BIN_ENV[config.harness_id]) or DEFAULT_BIN[config.harness_id]
    )


def child_environment(config: HarnessRunConfig, environ: Mapping[str, str]) -> dict[str, str]:
    env = {
        key: value
        for key, value in environ.items()
        if key in ENV_ALLOWLIST or key.startswith(ENV_ALLOWLIST_PREFIXES)
    }
    proxy_auth = (config.model_id and config.model_id.startswith("litellm/")) or config.harness_auth == "litellm"
    if proxy_auth:
        for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY",
                    "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
            env.pop(key, None)
    env.update(config.env)
    if proxy_auth:
        # Caller-supplied account keys are no safer than inherited ones. Harness
        # adapters translate the dedicated proxy key only after this boundary.
        for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY"):
            if not config.env.get("SEW_LITELLM_API_KEY") or env.get(key) != config.env["SEW_LITELLM_API_KEY"]:
                env.pop(key, None)
    return env


def classify_live_outcome(
    outcome: ProcessOutcome, summary: HarnessSummary, limits: LiveLimits | None = None
) -> TerminalStatus:
    """Map a finished child process to exactly one terminal status.

    Order matters: operator actions (cancel) and driver-enforced limits are
    facts about the run, so they win over anything the harness said. A clean
    answer wins over incidental error text. Only then is error text read, and
    auth is checked before provider availability because an expired login
    often also surfaces as a transport error.

    A budget is also exhausted when the cell finished but its final usage is
    over the cap. The streaming check cannot always catch that: Codex reports
    usage only when a turn completes, and Claude's per-message usage snapshots
    undercount output until the closing ``result`` event. An answer bought
    with more than the cell's budget must not be scored as a success.
    """

    if outcome.spawn_error is not None:
        return TerminalStatus(STATUS_HARNESS_BOOT_FAILED, CATEGORY_SPAWN_ERROR)
    if outcome.cancelled:
        return TerminalStatus(STATUS_CANCELLED, CATEGORY_CANCELLED)
    if outcome.budget_killed:
        return TerminalStatus(STATUS_BUDGET_EXHAUSTED, CATEGORY_TOKEN_BUDGET)
    if outcome.provider_budget_killed:
        return TerminalStatus(STATUS_BUDGET_EXHAUSTED, CATEGORY_PROVIDER_CALL_BUDGET)
    if outcome.capture_overflow:
        return TerminalStatus(STATUS_BUDGET_EXHAUSTED, CATEGORY_CAPTURE_BUDGET)
    if outcome.boot_timed_out:
        return TerminalStatus(STATUS_HARNESS_BOOT_FAILED, CATEGORY_NO_FIRST_OUTPUT)
    if outcome.timed_out:
        return TerminalStatus(STATUS_TIMEOUT, CATEGORY_TIMEOUT)
    if limits is not None:
        if (
            limits.max_total_tokens is not None
            and max(outcome.running_tokens, usage_total(summary.usage)) > limits.max_total_tokens
        ):
            return TerminalStatus(STATUS_BUDGET_EXHAUSTED, CATEGORY_TOKEN_BUDGET)
        if limits.max_provider_calls is not None and (
            outcome.provider_calls > limits.max_provider_calls
        ):
            return TerminalStatus(STATUS_BUDGET_EXHAUSTED, CATEGORY_PROVIDER_CALL_BUDGET)
    answered = bool(summary.final_text and summary.final_text.strip())
    if outcome.exit_code == 0 and answered and not summary.reported_error:
        if summary.usage is None:
            return TerminalStatus(STATUS_SUCCEEDED, TERMINAL_TOKEN_USAGE_UNKNOWN)
        return TerminalStatus(STATUS_SUCCEEDED, TERMINAL_COMPLETED)
    if summary.harness_budget_hit:
        return TerminalStatus(STATUS_BUDGET_EXHAUSTED, CATEGORY_HARNESS_BUDGET)
    error_text = "\n".join((*summary.error_messages, outcome.stderr_tail))
    if AUTH_ERROR_RE.search(error_text):
        return TerminalStatus(STATUS_HARNESS_BOOT_FAILED, CATEGORY_AUTH_FAILED)
    if PROVIDER_ERROR_RE.search(error_text):
        return TerminalStatus(STATUS_PROVIDER_UNAVAILABLE, CATEGORY_PROVIDER_UNAVAILABLE)
    if not outcome.ready:
        return TerminalStatus(STATUS_HARNESS_BOOT_FAILED, CATEGORY_EXITED_BEFORE_READY)
    if summary.reported_error:
        return TerminalStatus(STATUS_FAILED, CATEGORY_REPORTED_ERROR)
    if outcome.exit_code not in (0, None):
        return TerminalStatus(STATUS_FAILED, CATEGORY_EXIT_NONZERO)
    return TerminalStatus(STATUS_FAILED, CATEGORY_EMPTY_ANSWER)


def run_live_harness(
    config: HarnessRunConfig,
    output_root: Path,
    *,
    environ: Mapping[str, str] | None = None,
    cancel_event: threading.Event | None = None,
) -> HarnessRunResult:
    """Spawn the real harness for one cell and write its evidence bundle."""

    if config.mode != "live":
        raise SchemaError("run_live_harness requires mode='live'")
    source_env = dict(os.environ if environ is None else environ)
    from .oss import require_enabled
    from .host import HostUnavailable

    try:
        require_enabled(config.harness_id, config.model_id, source_env)
    except HostUnavailable as exc:
        raise LiveHarnessRefused(str(exc)) from None
    if not live_enabled(source_env):
        raise LiveHarnessRefused(
            f"live harness spawn is operator-gated; set {LIVE_ENV}=1 to run a real "
            f"{config.harness_id} (CI never sets it)"
        )
    if config.harness_id == "codex" and not config.model_id:
        # The isolated CODEX_HOME contains no model setting. Carry the model
        # selected by the source profile into --model so the child and its
        # durable spawn record agree. An absent setting stays unknown.
        selected, origin = codex_profile_model(source_env, config.model_profile)
        if selected:
            config = replace(config, model_id=selected, model_id_origin=origin)
    elif config.model_id and not config.model_id_origin:
        config = replace(config, model_id_origin="explicit")
    protocol = PROTOCOLS[config.harness_id]
    # Resolve known tasks even with a prompt override: production budgets and
    # provenance must not depend on which caller supplied the prompt. Unknown
    # ids remain available for ad-hoc smoke runs with an explicit prompt.
    gap_root = gap_task = None
    if config.provider_id in {"floor", "ceiling"}:
        from .gap.reference import resolve_gap_task

        gap_root, gap_task = resolve_gap_task(config)
    elif config.workspace_profile:
        from .gap.workspace import workspace_task

        gap_root, gap_task = workspace_task(config)
    elif config.task_source == "gap":
        # A brief task's search arms (native or a provider) take the same natural
        # prompt and budgets as its floor, from the GAP catalog. Without this they
        # fell through to the production catalog, which has no GAP ids, and every
        # live brief search cell raised "unknown task id".
        from .gap.reference import resolve_gap_task

        gap_root, gap_task = resolve_gap_task(config)
    try:
        resolved = None if gap_task is not None else resolve_task(config.task_id)
    except SchemaError as exc:
        if config.prompt_text is None or not str(exc).startswith("unknown task id:"):
            raise
        resolved = None
    if resolved is not None:
        if (
            resolved.source == "production"
            and config.prompt_text is not None
            and config.prompt_text != resolved.prompt
        ):
            raise SchemaError(
                f"production task {config.task_id} cannot override its catalog prompt"
            )
        prompt = resolved.prompt if config.prompt_text is None else config.prompt_text
        config = replace(config, task_source=resolved.source)
    elif gap_task is not None:
        from .gap.reference import reference_prompt

        prompt = (
            reference_prompt(config.provider_id, gap_task)
            if config.provider_id in {"floor", "ceiling"}
            else gap_task["prompt"]
        )
        config = replace(config, task_source="gap")
        budgets = gap_task["budgets"]
        config = replace(
            config,
            timeout_seconds=min(
                config.timeout_seconds or budgets["max_wall_clock_seconds"],
                budgets["max_wall_clock_seconds"],
            ),
            max_total_tokens=min(
                config.max_total_tokens or budgets["max_total_tokens"], budgets["max_total_tokens"]
            ),
            max_provider_calls=min(
                config.max_provider_calls
                if config.max_provider_calls is not None
                else budgets["max_provider_calls"],
                budgets["max_provider_calls"],
            ),
        )
    else:
        prompt = config.prompt_text
    limits = resolve_live_limits(config)
    run_id, run_dir = _claim_run_dir(output_root, config.run_id_override or _live_run_id(config))
    for rel in ("artifacts", "evidence", "evaluations", "metrics", "provider-calls", "sources"):
        (run_dir / rel).mkdir(parents=True, exist_ok=True)
    config = _metered(config, run_id=run_id, call_dir=run_dir / "provider-calls")

    binary = resolve_binary(config, source_env)
    auth_source = broker_auth.auth_source(config.harness_auth, source_env, model_id=config.model_id)
    auth_metadata: dict[str, str] = {"source": auth_source}
    broker_tokens: list[str] = []
    exposure = config.external_provider
    header = (exposure.mcp_server_config or {}).get("header_from_env") if exposure else None
    if isinstance(header, Mapping) and isinstance(header.get("value"), str):
        if header["value"]:
            broker_tokens.append(header["value"])
        parts = header["value"].split(None, 1)
        if len(parts) == 2 and parts[1].strip():
            broker_tokens.append(parts[1].strip())
    child_env: dict[str, str] = {}
    broker_log_dir = run_dir / "logs"
    with tempfile.TemporaryDirectory(prefix="sew-live-") as scratch, ExitStack() as boundaries:
        # A fresh scratch cwd keeps the harness from loading this repo's
        # CLAUDE.md/AGENTS.md, which would differ from what an arm is scored on.
        scratch_path = Path(scratch)
        last_message_path = scratch_path / "last-message.txt"
        workspace_path = scratch_path
        forbidden_paths = (gap_root,) if gap_root is not None else ()
        if config.workspace_profile:
            from .gap.workspace import prepare_workspace, prepare_python_environment

            pristine, workspace_path, offline_env = prepare_workspace(
                config, scratch_path, gap_root, gap_task
            )
            offline_env = prepare_python_environment(
                scratch_path, offline_env, {**source_env, **config.env}
            )
            config = replace(config, env={
                **{key: value for key, value in config.env.items() if not key.startswith("PIP_")},
                **offline_env,
            })
            if config.wheelhouse is not None:
                forbidden_paths += (Path(config.wheelhouse).resolve(),)
            forbidden_paths += (gap_root / "catalogs" / "gap" / gap_task["hidden"],)
        oss_model = bool(config.model_id and config.model_id.startswith("litellm/"))
        if config.provider_id == "native" and (
            oss_model or not config.native_search_available
            or not harnesses.get(config.harness_id).native_search
        ):
            # This harness has no native search, so the native arm cannot run.
            # Nothing spawns: the cell is recorded as unsupported instead of
            # letting the arm contract raise and take down the whole batch.
            # Same shape contract_for produces for a native arm (no provider_id),
            # so supported and unsupported cells aggregate identically.
            contract = ArmContract("native", config.harness_id)
            spawn_config = config
            argv = protocol.prompt_argv(binary, config, last_message_path, prompt)
            outcome = ProcessOutcome(ended_at=datetime.now(UTC))
            terminal = (
                TerminalStatus("not_applicable", "native-search-unavailable-on-oss-model")
                if oss_model else TerminalStatus("unsupported", TERMINAL_UNSUPPORTED_NATIVE_SEARCH)
            )
            auth_metadata = {"source": "none"}
            last_message = None
        else:
            surface = prepare_arm_spawn(config, scratch_path, source_env, harness_auth=auth_source)
            contract = surface.contract
            spawn_config = replace(
                config,
                harness_args=tuple(config.harness_args) + surface.harness_args,
                env={**config.env, **surface.env},
            )
            argv = protocol.prompt_argv(binary, spawn_config, last_message_path, prompt)
            try:
                if auth_source == "litellm":
                    from .oss import litellm_cell_env

                    auth_env = litellm_cell_env(config.harness_id, source_env)
                    if isinstance(protocol, ClaudeCodeProtocol):
                        auth_env.update(
                            ANTHROPIC_BASE_URL=auth_env["SEW_LITELLM_BASE_URL"],
                            ANTHROPIC_AUTH_TOKEN=auth_env["SEW_LITELLM_API_KEY"],
                        )
                    broker_tokens.append(auth_env["SEW_LITELLM_API_KEY"])
                    spawn_config = replace(spawn_config, env={**spawn_config.env, **auth_env}, harness_auth="litellm")
                    auth_metadata = {"source": "litellm"}
                if auth_source == "broker":
                    if config.harness_id == "claude-code":
                        auth_env = broker_auth.claude_code_env(broker_log_dir, environ=source_env)
                        auth_metadata = getattr(
                            auth_env,
                            "metadata",
                            {"source": "broker", "fingerprint": "", "expires_at": ""},
                        )
                        broker_tokens.append(auth_env["ANTHROPIC_AUTH_TOKEN"])
                        spawn_config = replace(
                            spawn_config,
                            env={**spawn_config.env, **auth_env},
                        )
                    else:
                        codex_home = Path(surface.env["CODEX_HOME"])
                        auth_metadata = broker_auth.codex_home_auth(
                            codex_home,
                            broker_log_dir,
                            environ=source_env,
                        )
                        tokens = json.loads((codex_home / "auth.json").read_text(encoding="utf-8"))[
                            "tokens"
                        ]
                        broker_tokens.extend(
                            value
                            for key in ("access_token", "id_token")
                            if isinstance(value := tokens.get(key), str) and value
                        )
                child_env = child_environment(spawn_config, source_env)
                if auth_source == "broker":
                    child_env.pop("ANTHROPIC_API_KEY", None)
                    child_env.pop("OPENAI_API_KEY", None)
                if config.workspace_profile:
                    from .gap.workspace import (
                        bench_network_boundary,
                        canary_command,
                        require_canary,
                    )

                    canary_path = run_dir / "artifacts" / "egress-canary.json"
                    harness_argv = list(argv)
                    argv, direct_evidence = boundaries.enter_context(
                        bench_network_boundary(
                            argv,
                            child_env,
                            cwd=workspace_path,
                            harness_id=config.harness_id,
                            refusal_path=canary_path,
                            environ=source_env,
                            harness_auth=auth_source,
                            wheelhouse=config.wheelhouse,
                        )
                    )
                    if config.harness_id == "claude-code":
                        from .gap.workspace import qualify_claude_code

                        qualify_claude_code(
                            harness_argv,
                            child_env,
                            cwd=workspace_path,
                            refusal_path=canary_path,
                            direct_evidence=direct_evidence,
                            command_prefix=argv[:-len(harness_argv)],
                        )
                    else:
                        nonce, command = canary_command(config.harness_id)
                        canary = spawn_and_capture(
                            argv,
                            prompt="Run this exact command once using your shell tool; "
                            "do not modify or summarize its output:\n" + command,
                            cwd=workspace_path,
                            env=child_env,
                            limits=LiveLimits(
                                min(limits.timeout_seconds, 120),
                                limits.boot_timeout_seconds,
                                limits.max_total_tokens,
                                limits.max_provider_calls,
                            ),
                            protocol=protocol,
                            cancel_event=cancel_event,
                            contract=contract,
                        )
                        from .gap.workspace import EgressCanaryRefused

                        canary_summary = protocol.summarize(
                            [e["event"] for e in canary.events], None
                        )
                        canary_evidence = {
                            "harness_id": config.harness_id,
                            "direct_egress": direct_evidence,
                            "admissible": False,
                            "events": canary.events,
                            "stderr": canary.stderr_tail,
                            "exit_code": canary.exit_code,
                            "usage": canary_summary.usage,
                        }
                        # Keep the failed probe too, and apply exact credential
                        # scrubbing before anything reaches the durable bundle.
                        canary_evidence = _scrub_canary_credentials(
                            scrub_value(canary_evidence), child_env
                        )
                        _write_json(canary_path, _elide_to_cap(canary_evidence))
                        if (
                            canary_summary.reported_error
                            or canary.exit_code != 0
                            or not canary.ready
                            or canary.timed_out
                            or canary.boot_timed_out
                            or canary.cancelled
                            or canary.budget_killed
                            or canary.provider_budget_killed
                            or canary.capture_overflow
                        ):
                            raise EgressCanaryRefused("GAP refused: canary cell did not complete")
                        record = require_canary(
                            canary.events, nonce, command, harness_id=config.harness_id
                        )
                        canary_evidence.update(admissible=True, probes=record["probes"])
                        _write_json(canary_path, _elide_to_cap(canary_evidence))
                        last_message_path.unlink(missing_ok=True)
                outcome = spawn_and_capture(
                    argv,
                    prompt=prompt,
                    cwd=workspace_path,
                    env=child_env,
                    limits=limits,
                    protocol=protocol,
                    cancel_event=cancel_event,
                    contract=contract,
                    provisional_meters_path=run_dir / PROVISIONAL_METERS_REF,
                )
            except (broker_auth.BrokerAuthError, HostUnavailable):
                outcome = ProcessOutcome(ended_at=datetime.now(UTC))
                terminal = TerminalStatus(STATUS_HARNESS_BOOT_FAILED, CATEGORY_AUTH_FAILED)
                diagnostic = broker_log_dir / f"{config.harness_id}-broker-auth-stderr.log"
                if diagnostic.is_file():
                    auth_metadata["diagnostic_ref"] = diagnostic.relative_to(run_dir).as_posix()
            else:
                terminal = None
            if config.workspace_profile:
                from .gap.workspace import capture_diff

                try:
                    capture_diff(
                        pristine,
                        workspace_path,
                        run_dir / "artifacts" / "workspace.diff",
                        forbidden_values=broker_tokens
                        + [
                            value
                            for key, value in child_env.items()
                            if value and TRANSCRIPT_SECRET_KEY_RE.search(key)
                        ],
                    )
                except SchemaError as exc:
                    # Refuse the patch while retaining the job's scrubbed
                    # transcript and diagnostics for investigation.
                    terminal = TerminalStatus(STATUS_FAILED, CATEGORY_WORKSPACE_DIFF_REFUSED)
                    outcome.stderr_tail = _fit_text(
                        outcome.stderr_tail
                        + "\nGAP workspace diff refused: "
                        + scrub_text(str(exc))
                    )
            last_message = (
                last_message_path.read_text(encoding="utf-8", errors="replace")
                if last_message_path.is_file()
                else None
            )
        arm_audit = audit_transcript(
            contract,
            [captured["event"] for captured in outcome.events],
            cwd=workspace_path,
            forbidden_paths=forbidden_paths,
            cell_env=config.env,
            new_packages=[p for p in gap_task["packages"] if p["role"] == "new"]
            if config.workspace_profile
            else (),
        )
    if broker_log_dir.is_dir():
        # The broker receipt and stderr belong to the durable cell, but must
        # pass the same credential scrub as every other piece of evidence.
        for path in broker_log_dir.iterdir():
            if path.is_file():
                try:
                    raw = path.read_text(encoding="utf-8", errors="replace")
                    if path.suffix == ".json":
                        try:
                            receipt = _elide_to_cap(scrub_value(json.loads(raw)))
                        except ValueError:
                            receipt = {"unparseable_receipt": True}
                        _write_json(path, receipt)
                    else:
                        path.write_text(_fit_text(scrub_text(raw)), encoding="utf-8")
                except OSError:
                    # An unreadable or unwritable log cannot enter the bundle
                    # unsanitized. Drop it and retain the terminal cell result.
                    path.unlink(missing_ok=True)
    if broker_tokens:
        # A child can echo credentials. Remove exact header values and broker
        # tokens before any artifact or summary is built.
        def remove_token(value: Any) -> Any:
            if isinstance(value, str):
                for token in broker_tokens:
                    if token:
                        value = value.replace(token, "<redacted:broker-token>")
                return value
            if isinstance(value, dict):
                return {key: remove_token(item) for key, item in value.items()}
            if isinstance(value, list):
                return [remove_token(item) for item in value]
            return value

        outcome.events = remove_token(outcome.events)
        outcome.stderr_tail = remove_token(outcome.stderr_tail)
        outcome.spawn_error = remove_token(outcome.spawn_error)
        last_message = remove_token(last_message)
        # Provider meters and broker logs can also echo child output.
        token_bytes = [token.encode("utf-8") for token in broker_tokens if token]
        for artifact in run_dir.rglob("*"):
            if artifact.is_file():
                try:
                    raw = artifact.read_bytes()
                    redacted = raw
                    for token in token_bytes:
                        redacted = redacted.replace(token, b"<redacted:broker-token>")
                    if redacted != raw:
                        artifact.write_bytes(redacted)
                except OSError:
                    # Retain no evidence we could not sanitize, and finish
                    # sweeping the remaining artifacts before bundling.
                    artifact.unlink(missing_ok=True)
    summary = protocol.summarize([captured["event"] for captured in outcome.events], last_message)
    if terminal is None:
        terminal = classify_live_outcome(outcome, summary, limits)
    if arm_audit.contaminated:
        terminal = TerminalStatus("contaminated", CATEGORY_CONTAMINATED)
    return _write_live_bundle(
        config,
        run_dir,
        run_id=run_id,
        prompt=prompt,
        argv=argv,
        limits=limits,
        outcome=outcome,
        summary=summary,
        terminal=terminal,
        passthrough_env=sorted(child_env),
        arm_contract=contract,
        arm_audit=arm_audit,
        auth_metadata=auth_metadata,
    )


def spawn_and_capture(
    argv: list[str],
    *,
    prompt: str,
    cwd: Path,
    env: Mapping[str, str],
    limits: LiveLimits,
    protocol: HarnessProtocol,
    cancel_event: threading.Event | None = None,
    clock: Callable[[], float] = time.monotonic,
    contract: ArmContract | None = None,
    provisional_meters_path: Path | None = None,
) -> ProcessOutcome:
    """Run the child to a terminal state, streaming its JSONL stdout.

    With an arm ``contract``, every search call the stream shows is metered
    against ``limits.max_provider_calls``.
    """

    outcome = ProcessOutcome()
    start = clock()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        outcome.spawn_error = f"{type(exc).__name__}: {exc.strerror or exc}"
        outcome.ended_at = datetime.now(UTC)
        _write_provisional_meters(provisional_meters_path, outcome)
        return outcome

    lines: queue.Queue[tuple[datetime, bytes] | None] = queue.Queue()
    stderr_chunks: list[bytes] = []
    capture_overflow = threading.Event()

    def _feed_stdin() -> None:
        try:
            assert proc.stdin is not None
            proc.stdin.write(prompt.encode("utf-8"))
            proc.stdin.close()
            outcome.prompt_sent_at = datetime.now(UTC)
        except (BrokenPipeError, OSError, ValueError):
            pass

    def _read_stdout() -> None:
        assert proc.stdout is not None
        budget = CAPTURE_BUDGET_BYTES
        captured = 0
        # The size bound keeps one newline-free flood from being read whole.
        for raw in iter(lambda: proc.stdout.readline(budget + 1), b""):
            captured += len(raw)
            if captured > budget:
                # Keep draining so the child never blocks on a full pipe
                # before it is killed, but hold nothing more.
                capture_overflow.set()
                continue
            lines.put((datetime.now(UTC), raw))
        lines.put(None)

    def _read_stderr() -> None:
        assert proc.stderr is not None
        size = 0
        for chunk in iter(lambda: proc.stderr.read(4096), b""):
            stderr_chunks.append(chunk)
            size += len(chunk)
            while size > STDERR_TAIL_BYTES * 2 and len(stderr_chunks) > 1:
                size -= len(stderr_chunks.pop(0))

    threads = [
        threading.Thread(target=target, daemon=True)
        for target in (_feed_stdin, _read_stdout, _read_stderr)
    ]
    for thread in threads:
        thread.start()

    usage_seen: dict[str, int] = {}
    calls_seen: set[str] = set()
    stdout_done = False
    session_seen: set = set()
    session_bytes = 0
    try:
        while True:
            try:
                item = lines.get(timeout=0.05)
            except queue.Empty:
                item = False
            if item is None:
                stdout_done = True
            elif item is not False:
                received_at, raw = item
                _absorb_line(outcome, protocol, raw, received_at, usage_seen, contract, calls_seen)
                if (
                    limits.max_total_tokens is not None
                    and outcome.running_tokens > limits.max_total_tokens
                ):
                    outcome.budget_killed = True
                    break
                if (
                    limits.max_provider_calls is not None
                    and outcome.provider_calls > limits.max_provider_calls
                ):
                    outcome.provider_budget_killed = True
                    break
            poll = getattr(protocol, "poll_events", None)
            if poll is not None:
                for event in poll(env, session_seen):
                    raw = json.dumps(event).encode()
                    session_bytes += len(raw)
                    if session_bytes > CAPTURE_BUDGET_BYTES:
                        outcome.capture_overflow = True
                        break
                    _absorb_line(outcome, protocol, raw, datetime.now(UTC),
                                 usage_seen, contract, calls_seen)
                if limits.max_total_tokens is not None and outcome.running_tokens > limits.max_total_tokens:
                    outcome.budget_killed = True
                    break
                if limits.max_provider_calls is not None and outcome.provider_calls > limits.max_provider_calls:
                    outcome.provider_budget_killed = True
                    break
            if outcome.capture_overflow or capture_overflow.is_set():
                outcome.capture_overflow = True
                break
            # Deadlines are checked on every pass, not only when stdout is
            # quiet: a harness that streams forever must still time out.
            if stdout_done and _leader_exited(proc):
                break
            if cancel_event is not None and cancel_event.is_set():
                outcome.cancelled = True
                break
            elapsed = clock() - start
            if not outcome.ready and elapsed > limits.boot_timeout_seconds:
                outcome.boot_timed_out = True
                break
            if elapsed > limits.timeout_seconds:
                outcome.timed_out = True
                break
    finally:
        # Also runs on KeyboardInterrupt: the child never outlives the driver.
        _terminate(proc)
        for thread in threads:
            thread.join(timeout=KILL_GRACE_SECONDS)
        # Drain anything the reader queued between the break and the kill.
        while True:
            try:
                item = lines.get_nowait()
            except queue.Empty:
                break
            if item is not None:
                _absorb_line(outcome, protocol, item[1], item[0], usage_seen, contract, calls_seen)
        outcome.exit_code = proc.returncode
        outcome.stderr_tail = b"".join(stderr_chunks)[-STDERR_TAIL_BYTES:].decode(
            "utf-8", errors="replace"
        )
        outcome.ended_at = datetime.now(UTC)
        _write_provisional_meters(provisional_meters_path, outcome)
    return outcome


def _write_provisional_meters(path: Path | None, outcome: ProcessOutcome) -> None:
    if path is None:
        return
    ended_at = outcome.ended_at or datetime.now(UTC)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(
        path,
        {
            "schema_version": 1,
            "kind": "sew.live_provisional_meters",
            "started_at": _iso(outcome.started_at),
            "ended_at": _iso(ended_at),
            "process": {
                "running_tokens": outcome.running_tokens,
                "provider_calls": outcome.provider_calls,
                "event_count": len(outcome.events),
                "budget_killed": outcome.budget_killed,
                "provider_budget_killed": outcome.provider_budget_killed,
                "timed_out": outcome.timed_out,
                "boot_timed_out": outcome.boot_timed_out,
                "cancelled": outcome.cancelled,
                "capture_overflow": outcome.capture_overflow,
            },
        },
    )


def _absorb_line(
    outcome: ProcessOutcome,
    protocol: HarnessProtocol,
    raw: bytes,
    received_at: datetime,
    usage_seen: dict[str, int],
    contract: ArmContract | None = None,
    calls_seen: set[str] | None = None,
) -> None:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return
    try:
        event = json.loads(text)
    except json.JSONDecodeError:
        event = None
    if not isinstance(event, dict):
        event = {"type": "sew.raw_stdout", "text": text}
    outcome.events.append({"received_at": _iso_ms(received_at), "event": event})
    outcome.last_event_at = received_at
    if outcome.ready_at is None and protocol.is_ready(event):
        outcome.ready = True
        outcome.ready_at = received_at
    if outcome.first_output_at is None and protocol.is_output(event):
        outcome.first_output_at = received_at
    protocol.running_usage(event, usage_seen)
    outcome.running_tokens = sum(usage_seen.values())
    if contract is not None and calls_seen is not None:
        for index, (call_id, _) in enumerate(provider_tool_calls(contract, event)):
            # An id-less call is its own call; an id seen before is the same one.
            calls_seen.add(call_id or f"event-{len(outcome.events)}-{index}")
        outcome.provider_calls = len(calls_seen)


def _leader_exited(proc: subprocess.Popen[bytes]) -> bool:
    """True once the harness (the group leader) has exited, without reaping it.

    ``Popen.poll`` would reap the leader. Once reaped, its PID, which is also
    the process-group id, can be recycled, and ``_terminate`` could then signal
    an unrelated group. ``WNOWAIT`` leaves the leader a zombie until
    ``_terminate`` has finished with the group.
    """

    if proc.returncode is not None:
        return True
    if hasattr(os, "waitid"):
        try:
            state = os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            return True
        return state is not None
    # macOS Python 3.11/3.12 expose kqueue but not waitid. NOTE_EXIT observes
    # termination without reaping, preserving the process-group PID guard.
    with closing(select.kqueue()) as events:
        event = select.kevent(
            proc.pid, filter=select.KQ_FILTER_PROC,
            flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
            fflags=select.KQ_NOTE_EXIT,
        )
        try:
            return bool(events.control([event], 1, 0))
        except ProcessLookupError:
            # Registering an already-exited zombie may return ESRCH.
            return True


def _signal_group(proc: subprocess.Popen[bytes], sig: signal.Signals) -> None:
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        # PermissionError is what macOS returns for a group whose only member
        # is the unreaped (zombie) leader.
        pass


def _terminate(proc: subprocess.Popen[bytes]) -> None:
    """SIGTERM the child's process group, then SIGKILL whatever is left of it.

    The harness runs in its own session, so the group also holds any MCP
    servers or tool subprocesses it started; none of them may outlive the cell.
    The leader gets the grace period to exit on SIGTERM, and then the group is
    SIGKILLed unconditionally: a straggler that ignores SIGTERM would otherwise
    survive a leader that exited promptly. The leader is reaped only after that
    last signal, so the group id cannot have been recycled while we signal it.
    """

    if proc.returncode is not None:
        # Already reaped by someone else: the group id may belong to another
        # process group now, so signalling it is unsafe.
        return
    _signal_group(proc, signal.SIGTERM)
    deadline = time.monotonic() + KILL_GRACE_SECONDS
    while not _leader_exited(proc) and time.monotonic() < deadline:
        time.sleep(0.05)
    _signal_group(proc, signal.SIGKILL)
    try:
        proc.wait(timeout=KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def _write_live_bundle(
    config: HarnessRunConfig,
    run_dir: Path,
    *,
    run_id: str,
    prompt: str,
    argv: list[str],
    limits: LiveLimits,
    outcome: ProcessOutcome,
    summary: HarnessSummary,
    terminal: TerminalStatus,
    passthrough_env: list[str],
    arm_contract: ArmContract,
    arm_audit: Any,
    auth_metadata: dict[str, str],
) -> HarnessRunResult:
    ended_at = outcome.ended_at or datetime.now(UTC)
    availability = None  # Resolve from full evidence before overflow truncates it.
    if arm_contract.kind == "provider":
        availability = provider_available(
            run_dir,
            config.provider_id,
            run_id,
            terminal=terminal,
            transcript=_live_transcript(outcome, summary, prompt, None, ended_at),
            stderr=outcome.stderr_tail,
        )
        if availability is False:
            terminal = TerminalStatus(STATUS_PROVIDER_UNAVAILABLE, "provider_tools_unavailable")
    # Oversize policy, applied to every artifact before it is written, because
    # an artifact over the cap would abort the cell after it ran:
    # * final answer: a succeeded cell whose answer cannot fit fails as
    #   final_answer_over_artifact_cap (a truncated answer must not be scored);
    # * transcript: fitted by eliding strings; if even that overflows, a
    #   succeeded cell fails as transcript_over_artifact_cap and keeps a
    #   marked head. A cell that already failed keeps its own classification;
    # * prompt and spawn metadata: elided to fit; they are inputs, not results;
    # * stderr: bounded when it is captured (STDERR_TAIL_BYTES).
    final_text = summary.final_text if terminal.status == STATUS_SUCCEEDED else None
    final_answer = scrub_value(_final_answer(final_text))
    if not _fits_artifact_cap(final_answer):
        if terminal.status == STATUS_SUCCEEDED:
            terminal = TerminalStatus(STATUS_FAILED, CATEGORY_ANSWER_OVERFLOW)
        final_answer = _elide_to_cap(final_answer)
        final_text = None
    transcript = _live_transcript(outcome, summary, prompt, final_text, ended_at)
    try:
        fitted_transcript = fit_transcript(transcript)
    except TranscriptOverflow:
        # Raising here would take down the whole operator batch. A succeeded
        # cell fails instead: its tool-call record can no longer be kept
        # whole, so it must never be scored as an answer.
        if terminal.status == STATUS_SUCCEEDED:
            terminal = TerminalStatus(STATUS_FAILED, CATEGORY_TRANSCRIPT_OVERFLOW)
            final_text = None
            final_answer = scrub_value(_final_answer(None))
            transcript = _live_transcript(outcome, summary, prompt, final_text, ended_at)
        fitted_transcript = overflow_transcript(transcript)

    _write_json(run_dir / "artifacts" / "final-answer.json", final_answer)
    _write_text(run_dir / "artifacts" / "prompt.md", _fit_text(scrub_text(prompt)))
    _write_json(run_dir / "artifacts" / "transcript.json", fitted_transcript)
    _write_text(run_dir / "artifacts" / "harness-stderr.txt", scrub_text(outcome.stderr_tail))
    _write_json(
        run_dir / "artifacts" / "spawn-metadata.json",
        _elide_to_cap(
            _spawn_metadata(
                config,
                argv,
                limits,
                outcome,
                passthrough_env,
                arm_contract=arm_contract,
                arm_audit=arm_audit,
                auth_metadata=auth_metadata,
            )
        ),
    )

    provider_calls = _collect_provider_calls(run_dir, run_id)
    provider_call_refs = [ref for ref, _ in provider_calls]
    run_record = {
        "schema_version": 1,
        "run_id": run_id,
        "suite_id": config.suite_id,
        "task_id": config.task_id,
        "task_source": config.task_source,
        "provider_id": config.provider_id,
        "harness_id": config.harness_id,
        "model_profile": config.model_profile,
        "mode": "live",
        "status": terminal.status,
        "started_at": _iso(outcome.started_at),
        "ended_at": _iso(ended_at),
        "metrics_ref": "metrics/metrics.json",
        "evaluation_ref": "evaluations/evaluation.json",
        "evidence_bundle_ref": "evidence/bundle.yaml",
        "provider_call_refs": provider_call_refs,
    }
    if arm_contract.kind == "provider":
        run_record["provider_availability"] = {True: "available", False: "unavailable"}.get(
            availability, "unknown"
        )
    if terminal.category != TERMINAL_COMPLETED:
        run_record["failure_category"] = terminal.category
    _write_json(run_dir / "run.json", run_record)

    # The driver captures; it does not grade. The evaluator/judge (WSB-02,
    # WSB-07) replaces this record, so nothing here claims a quality verdict.
    evaluation = {
        "schema_version": 1,
        "run_id": run_id,
        "task_id": config.task_id,
        "outcome": "not_applicable",
        "dimensions": {
            "completed": terminal.status == STATUS_SUCCEEDED,
            "correct": False,
            "grounded": False,
            "fresh": False,
            "schema_valid": False,
            "safe": False,
        },
        "failure_reasons": ["evaluation_pending"]
        + ([] if terminal.status == STATUS_SUCCEEDED else [terminal.category]),
        "judge": {"kind": "pending", "validator": "live_harness_driver"},
        "evidence_refs": [],
    }
    _write_json(run_dir / "evaluations" / "evaluation.json", evaluation)
    metrics = normalize_run_metrics(
        run_record=run_record,
        provider_calls=[record for _, record in provider_calls],
        sources=[],
        evaluation_record=evaluation,
        harness_usage_rows=[summary.usage] if summary.usage is not None else [],
        transcript=transcript,
    )
    if config.model_id and config.model_id.startswith("litellm/"):
        from .cost_model import load_price_table, model_cost

        metrics["token_usage"]["usage_basis"] = "harness" if summary.usage is not None else "unavailable"
        _write_json(
            run_dir / "artifacts" / "model-cost.json",
            model_cost(config.model_id, metrics["token_usage"], load_price_table()),
        )
    _write_json(run_dir / "metrics" / "metrics.json", metrics)

    artifacts = []
    for rel, media_type, redaction in (
        ("artifacts/final-answer.json", "application/json", "sanitized"),
        ("artifacts/prompt.md", "text/markdown", "sanitized"),
        ("artifacts/transcript.json", "application/json", "secrets-redacted"),
        ("artifacts/harness-stderr.txt", "text/plain", "secrets-redacted"),
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
    for rel, media_type in (
        (AVAILABILITY_REF, "application/json"),
        (OBSERVATION_FAILURE_REF, "text/plain"),
        ("artifacts/model-cost.json", "application/json"),
        ("artifacts/workspace.diff", "text/x-diff"),
        ("artifacts/egress-canary.json", "application/json"),
    ):
        path = run_dir / rel
        if path.is_file():
            artifacts.append(
                {
                    "path": rel,
                    "media_type": media_type,
                    "size_bytes": path.stat().st_size,
                    "redaction": "secrets-redacted",
                }
            )
    for path in sorted((run_dir / "logs").glob("*")):
        if path.is_file():
            artifacts.append(
                {
                    "path": path.relative_to(run_dir).as_posix(),
                    "media_type": "application/json" if path.suffix == ".json" else "text/plain",
                    "size_bytes": path.stat().st_size,
                    "redaction": "secrets-redacted",
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
            "normalized_sources": [],
        },
    }
    _write_yaml(run_dir / "evidence" / "bundle.yaml", bundle)

    validate_fixture_run(run_dir)
    assert_no_secret_material(run_dir)
    return HarnessRunResult(
        run_id=run_id,
        status=terminal.status,  # type: ignore[arg-type]
        failure_category=run_record.get("failure_category"),
        bundle_dir=run_dir,
        evidence_bundle_ref="evidence/bundle.yaml",
        token_accounting_source=metrics["token_usage"]["accounting_source"],
    )


def _live_transcript(
    outcome: ProcessOutcome,
    summary: HarnessSummary,
    prompt: str,
    final_text: str | None,
    ended_at: datetime,
) -> list[dict[str, Any]]:
    """Marker events metrics.py reads for latency, around the harness stream.

    Harness events are kept whole under ``harness_event`` so WSB-06 can re-verify
    every tool call an arm made against its declared tool surface.
    """

    transcript: list[dict[str, Any]] = []
    if outcome.prompt_sent_at is not None:
        transcript.append(_marker("prompt_sent", outcome.prompt_sent_at))
    transcript.append({"role": "user", "content": prompt})
    if outcome.ready_at is not None:
        transcript.append(_marker("harness_ready", outcome.ready_at))
    if outcome.first_output_at is not None:
        transcript.append(_marker("first_output_token", outcome.first_output_at))
    for captured in outcome.events:
        transcript.append(
            {
                "role": "harness",
                "received_at": captured["received_at"],
                "harness_event": captured["event"],
            }
        )
    if final_text is not None:
        transcript.append(
            {
                "event": "final_answer",
                "timestamp": _iso(outcome.last_event_at or ended_at),
                "role": "assistant",
                "content": final_text,
            }
        )
    if summary.usage is not None:
        transcript.append({"role": "telemetry", "usage": summary.usage})
    return scrub_value(transcript)


def fit_transcript(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shrink long string values until the transcript fits the artifact cap.

    Strings are shortened instead of events being dropped: every event, and so
    every tool call, survives for the WSB-06 contamination check.
    """

    for limit in (None, 4000, 1500, 500, 160, 0):
        candidate = transcript if limit is None else _truncate_strings(transcript, limit)
        if _fits_artifact_cap(candidate):
            return candidate
    raise TranscriptOverflow("live transcript exceeds the evidence artifact cap even when elided")


def overflow_transcript(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the longest fully elided head that fits the cap, plus a marker.

    This is only for a cell already failed as ``transcript_over_artifact_cap``.
    The marker records how many events were dropped, so the incompleteness is
    visible in the bundle rather than silent.
    """

    elided = _truncate_strings(transcript, 0)
    keep = len(elided)
    while True:
        candidate = elided[:keep] + [
            {
                "event": "sew.transcript_overflow",
                "role": "system",
                "events_total": len(elided),
                "events_kept": keep,
                "cap_bytes": ARTIFACT_CAP_BYTES,
            }
        ]
        if keep == 0 or _fits_artifact_cap(candidate):
            return candidate
        keep //= 2


def _fits_artifact_cap(value: Any) -> bool:
    encoded = json.dumps(value, indent=2, sort_keys=True) + "\n"
    return len(encoded.encode("utf-8")) <= ARTIFACT_CAP_BYTES


def _elide_to_cap(value: Any) -> Any:
    """Shorten string values (never protocol fields) until ``value`` fits.

    Falls back to a marker object when even full elision cannot fit.
    """

    for limit in (None, 4000, 1500, 500, 160, 0):
        candidate = value if limit is None else _truncate_strings(value, limit)
        if _fits_artifact_cap(candidate):
            return candidate
    return {"elided": True, "reason": "exceeds the evidence artifact cap"}


def _fit_text(text: str) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= ARTIFACT_CAP_BYTES:
        return text
    keep = encoded[: ARTIFACT_CAP_BYTES - 200].decode("utf-8", errors="ignore")
    return (
        f"{keep}\n<elided:{len(encoded) - len(keep.encode('utf-8'))} bytes over the artifact cap>\n"
    )


def _metered(config: HarnessRunConfig, *, run_id: str, call_dir: Path) -> HarnessRunConfig:
    """Put a provider arm's stdio MCP server behind ``sew.mcp_meter``.

    The vendor server, its tools and its environment are unchanged; the meter
    only relays and records each tool call under ``call_dir``, so the run's
    vendor spend can be priced instead of reported as unknown. A remote (URL)
    server cannot be wrapped and stays unmetered.
    """

    exposure = config.external_provider
    if exposure is None or not exposure.mcp_server_config:
        return config
    wrapped = metered_server_config(
        exposure.mcp_server_config,
        provider_id=exposure.provider_id,
        run_id=run_id,
        call_dir=call_dir,
    )
    if wrapped is None:
        return config
    return replace(config, external_provider=replace(exposure, mcp_server_config=wrapped))


def _collect_provider_calls(run_dir: Path, run_id: str) -> list[tuple[str, dict[str, Any]]]:
    """The metered provider-call records a run wrote, oldest first.

    A record the meter left half-written (a dotfile) or one that does not
    validate is not referenced; the run is still recorded, with that call
    unpriced.
    """

    calls = []
    for path in sorted((run_dir / "provider-calls").glob("*.json")):
        if path.name.startswith(".") or path.name == "availability.json":
            continue
        try:
            record = validate_provider_call(
                json.loads(path.read_text(encoding="utf-8")), expected_run_id=run_id
            )
        except (OSError, ValueError, SchemaError):
            continue
        calls.append((f"provider-calls/{path.name}", record))
    calls.sort(key=lambda item: (str(item[1].get("started_at")), item[0]))
    return calls


def _claim_run_dir(output_root: Path, run_id: str) -> tuple[str, Path]:
    """Create the cell's run directory exclusively. Existing evidence is never deleted.

    A directory that already exists (a retry or resume reusing the id, or a
    completed run) keeps its bundle; this run takes the next free ``-rN`` id.
    """

    if not SAFE_RUN_ID_RE.fullmatch(run_id) or ".." in run_id:
        raise SchemaError(f"run id must be a single safe path component, got {run_id!r}")
    output_root.mkdir(parents=True, exist_ok=True)
    candidate, attempt = run_id, 1
    while True:
        run_dir = output_root / candidate
        try:
            run_dir.mkdir()
        except FileExistsError:
            attempt += 1
            candidate = f"{run_id}-r{attempt}"
            continue
        return candidate, run_dir


def scrub_text(text: str) -> str:
    """Strip credential material with the fleet's HRR-09 vocabulary."""

    text = AUTHORIZATION_BEARER_HEADER_RE.sub(
        r"\1<redacted:bearer-header>",
        text,
    )
    text = BEARER_PLACEHOLDER_RE.sub("<redacted:bearer-placeholder>", text)
    text = COOKIE_HEADER_RE.sub("<redacted:cookie-header>", text)
    return _fleet_scrub()(text)


def scrub_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "<redacted>"
                if isinstance(key, str) and TRANSCRIPT_SECRET_KEY_RE.search(key)
                else scrub_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [scrub_value(item) for item in value]
    if isinstance(value, str):
        return scrub_text(value)
    return value


def _fleet_scrub() -> Callable[[str], str]:
    from .provider_errors import scrub_provider_error_text

    return scrub_provider_error_text


def _spawn_metadata(
    config: HarnessRunConfig,
    argv: list[str],
    limits: LiveLimits,
    outcome: ProcessOutcome,
    passthrough_env: list[str],
    *,
    arm_contract: ArmContract,
    arm_audit: Any,
    auth_metadata: dict[str, str],
) -> dict[str, Any]:
    if config.provider_id in {"native", "no-search", "floor", "ceiling"}:
        provider = {
            "mode": config.provider_id,
            "native_search_available": config.native_search_available,
        }
    else:
        provider = {
            "mode": "external",
            "provider_id": config.provider_id,
            "tool_name": config.external_provider.tool_name if config.external_provider else None,
        }
    boot_ms = _delta_ms(outcome.started_at, outcome.ready_at)
    record = scrub_value(
        {
            "harness_id": config.harness_id,
            "model_profile": config.model_profile,
            "model_id": config.model_id,
            "model_id_origin": config.model_id_origin,
            "mode": "live",
            "argv": _redact(argv),
            "cwd": "<scratch>",
            # Values only for known non-secret settings; everything else by name.
            "env": {
                key: (value if key in ENV_VALUES_RECORDED else "<redacted>")
                for key, value in sorted(config.env.items())
            },
            # Names only: the values include harness auth and are never written.
            "env_passthrough": passthrough_env,
            "provider_exposure": provider,
            "arm_contract": arm_contract.as_record(),
            "arm_audit": {
                "contaminated": arm_audit.contaminated,
                "observed_tool_calls": list(arm_audit.observed_tool_calls),
                "violations": list(arm_audit.violations),
                "new_package_attempts": arm_audit.new_package_attempts,
                "denied_network_attempts": arm_audit.denied_network_attempts,
                "config_neutralized_network_attempts": arm_audit.config_neutralized_network_attempts,
            },
            "limits": {
                "timeout_seconds": limits.timeout_seconds,
                "boot_timeout_seconds": limits.boot_timeout_seconds,
                "max_total_tokens": limits.max_total_tokens,
                "max_provider_calls": limits.max_provider_calls,
            },
            "process": {
                "exit_code": outcome.exit_code,
                "spawn_error": outcome.spawn_error,
                "ready": outcome.ready,
                "boot_latency_ms": boot_ms,
                "timed_out": outcome.timed_out,
                "boot_timed_out": outcome.boot_timed_out,
                "cancelled": outcome.cancelled,
                "budget_killed": outcome.budget_killed,
                "provider_budget_killed": outcome.provider_budget_killed,
                "capture_overflow": outcome.capture_overflow,
                "running_tokens": outcome.running_tokens,
                "provider_calls": outcome.provider_calls,
                "event_count": len(outcome.events),
            },
        }
    )

    record["harness_auth"] = auth_metadata
    return record


def _final_answer(final_text: str | None) -> dict[str, Any]:
    """The deliverable object the evaluator scores.

    When the harness answered with a JSON object (bare or fenced) that object
    is the deliverable. Otherwise the text is wrapped. No arm, harness, or
    provider identifier is added: the blinded judge reads this file.
    """

    if final_text is None:
        return {"answer": None, "citation_urls": []}
    parsed = _parse_json_object(final_text)
    if parsed is not None:
        return parsed
    return {"answer": final_text, "citation_urls": parse_citations(final_text)}


FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    for candidate in (text.strip(), *FENCED_JSON_RE.findall(text)):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def usage_total(row: Mapping[str, Any] | None) -> int:
    """Every token in a disjoint usage row: what a cell's token budget counts.

    Cached input and boot context count. They are billed, the suite budget
    tracker sums the same four buckets, and a provider arm's MCP tool schemas
    are a real part of what that arm costs.
    """

    if not row:
        return 0
    return sum(_int(row.get(key)) for key in ("input", "cached_input", "output", "reasoning"))


def _claude_total(usage: Mapping[str, Any]) -> int:
    return sum(
        _int(usage.get(key))
        for key in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "output_tokens",
        )
    )


def _claude_usage_row(usage: Any) -> dict[str, int] | None:
    """Disjoint buckets: input + cached_input + output + reasoning = total.

    Cache writes remain in input for the disjoint budget buckets. Their count
    and optional TTL split are also retained as pricing metadata; thinking
    tokens are moved out of output rather than counted twice.
    """

    if not isinstance(usage, Mapping) or "output_tokens" not in usage:
        return None
    details = usage.get("output_tokens_details")
    reasoning = _int(details.get("thinking_tokens")) if isinstance(details, Mapping) else 0
    output = _int(usage.get("output_tokens"))
    cache_write = _int(usage.get("cache_creation_input_tokens"))
    row = {
        "input": _int(usage.get("input_tokens")) + cache_write,
        "cached_input": _int(usage.get("cache_read_input_tokens")),
        "output": max(0, output - reasoning),
        "reasoning": min(reasoning, output),
    }
    row["total_billable"] = sum(row.values())
    row["cache_write"] = cache_write
    creation = usage.get("cache_creation")
    if isinstance(creation, Mapping):
        for field, source in (
            ("cache_write_5m", "ephemeral_5m_input_tokens"),
            ("cache_write_1h", "ephemeral_1h_input_tokens"),
        ):
            if source in creation:
                row[field] = _int(creation.get(source))
    return row


def _codex_usage_row(rows: list[Mapping[str, Any]]) -> dict[str, int] | None:
    """Same disjoint buckets; Codex counts cached tokens inside input_tokens."""

    if not rows:
        return None
    total = {"input": 0, "cached_input": 0, "output": 0, "reasoning": 0}
    for usage in rows:
        cached = _int(usage.get("cached_input_tokens"))
        output = _int(usage.get("output_tokens"))
        reasoning = min(_int(usage.get("reasoning_output_tokens")), output)
        total["input"] += max(0, _int(usage.get("input_tokens")) - cached)
        total["cached_input"] += cached
        total["output"] += output - reasoning
        total["reasoning"] += reasoning
    total["total_billable"] = sum(total.values())
    return total


def _truncate_strings(value: Any, limit: int) -> Any:
    if isinstance(value, dict):
        return {
            key: item
            if key in PROTOCOL_KEYS and isinstance(item, str)
            else _truncate_strings(item, limit)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_truncate_strings(item, limit) for item in value]
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"<elided:{len(value) - limit} chars>"
    return value


def _marker(name: str, at: datetime) -> dict[str, Any]:
    return {"event": name, "timestamp": _iso(at), "role": "system"}


def _live_run_id(config: HarnessRunConfig) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    provider = config.provider_id.replace("-", "_")
    return f"live-{config.harness_id}-{provider}-{config.task_id}-{stamp}"


def _delta_ms(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def _iso_ms(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return max(0, int(value))


__all__ = [
    "LIVE_ENV",
    "ClaudeCodeProtocol",
    "CodexProtocol",
    "LiveHarnessRefused",
    "LiveLimits",
    "ProcessOutcome",
    "child_environment",
    "classify_live_outcome",
    "fit_transcript",
    "live_enabled",
    "resolve_live_limits",
    "run_live_harness",
    "scrub_text",
    "scrub_value",
    "spawn_and_capture",
    "usage_total",
]
