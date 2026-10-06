"""Metering proxy for a live arm's provider MCP server.

A live bakeoff arm reaches its provider through the vendor's own MCP server, so
the agent sees exactly the tools that vendor ships. Those servers report no
spend, which left every provider arm's cost `unknown`. This proxy sits between
the harness and the vendor server on the stdio transport:

    harness  <-- stdio -->  sew.mcp_meter  <-- stdio -->  vendor MCP server

It relays every line unchanged in both directions. On the side it matches each
``tools/call`` request to its response and writes one provider-call record per
call into the run's ``provider-calls/`` directory. Spend is filled in from the
most direct evidence available:

1. **measured**: the vendor response itself reports it (Exa ``costDollars``,
   Perplexity ``usage.cost``, Tavily ``usage.credits``, Firecrawl
   ``creditsUsed`` or inline Alexandria ``data.creditsCost``);
2. **tariff**: ``config/mcp-meter-tariffs.yaml`` maps the tool to a published
   list-price tier or a published credit rule, and the price table prices it;
3. otherwise nothing, and the call stays unpriced, never guessed.

Fail-open: metering is best effort and must never change what the harness
and the vendor server say to each other. The relay is the product; a record
is a by-product.

The transparent relay and pricing helpers are vendored locally for standalone
runs. The selected host receives best-effort metering calls in addition to the
existing run-local records; host failures never interrupt the relay.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, BinaryIO

if TYPE_CHECKING:
    from .harness import TerminalStatus


CORE_UNAVAILABLE = "mcp_metering_core_unavailable"
TARIFFS_FILE = "mcp-meter-tariffs.yaml"
RECORD_SCHEMA_VERSION = 1
UNKNOWN_TOOL = "unknown"
AVAILABILITY_REF = "provider-calls/availability.json"
OBSERVATION_FAILED = "mcp_meter_snapshot_write_failed"
OBSERVATION_FAILURE_REF = "artifacts/meter-observation-failed.txt"
RATE_LIMIT_MARKERS = ("429", "rate limit", "rate-limit", "too many requests")
BRAVE_WEB_EMPTY_RESULT = "No web results found"
# Names of ordinary tool inputs that are safe to describe in a published record.
# Pricing selectors are added from the checked-in tariff for the called tool.
KNOWN_ARGUMENT_KEYS = frozenset({"query", "url", "messages"})


_core_cache: list[SimpleNamespace | None] = []


def _core() -> SimpleNamespace | None:
    """The shared metering core, or ``None`` (reported once) when it cannot be imported."""

    if not _core_cache:
        try:
            from . import meter_core as mcp_metering
            from . import meter_pricing as pricing
        except ImportError as exc:
            print(
                f"sew.mcp_meter: {CORE_UNAVAILABLE} ({exc}); calls stay unmetered", file=sys.stderr
            )
            _core_cache.append(None)
        else:
            _core_cache.append(
                SimpleNamespace(
                    load_tariffs=mcp_metering.load_tariffs,
                    serve=mcp_metering.serve,
                    import_root=str(Path(mcp_metering.__file__).resolve().parents[1]),
                    argument_specs=pricing._argument_specs,
                    measured_spend=pricing.measured_spend,
                    response_documents=pricing.response_documents,
                    tariff_spend=pricing.tariff_spend,
                )
            )
    return _core_cache[0]


def default_tariffs_path() -> Path:
    from .catalog import module_root

    return module_root() / "config" / TARIFFS_FILE


def load_tariffs(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Per-provider tool tariffs; an unreadable file meters without tariffs."""

    core = _core()
    return core.load_tariffs(path or default_tariffs_path()) if core else {}


# --------------------------------------------------------------------------
# Pricing evidence for one call
# --------------------------------------------------------------------------


def recognized_argument_keys(rule: Mapping[str, Any] | None) -> frozenset[str]:
    """Only checked-in argument names may appear in a provider-call record."""

    core = _core()
    if not rule or core is None:
        return KNOWN_ARGUMENT_KEYS
    names = {str(spec["arg"]) for spec in core.argument_specs(rule)}
    extra = rule.get("extra_credits_if_arg_contains")
    if isinstance(extra, Mapping) and extra.get("arg"):
        names.add(str(extra["arg"]))
    names.update(str(name) for name in rule.get("unpriced_if_args") or [])
    for key in ("request_count_arg", "result_limit_arg"):
        if isinstance(rule.get(key), str):
            names.add(rule[key])
    return KNOWN_ARGUMENT_KEYS | names


def pricing_arguments(
    rule: Mapping[str, Any] | None, arguments: Mapping[str, Any]
) -> dict[str, Any]:
    """Keep tariff enums and positive integer result limits as pricing evidence.

    Free-text arguments (queries, URLs, prompts) never enter a record, so a
    published bundle cannot carry a confidential search. Arguments that make a
    call unpriced are kept as presence flags, not values.
    """

    core = _core()
    if not rule or core is None:
        return {}
    kept: dict[str, Any] = {}
    for spec in core.argument_specs(rule):
        name = str(spec["arg"])
        value = arguments.get(name)
        if isinstance(value, str) and value in (spec.get("values") or {}):
            kept[name] = value
        elif value not in (None, ""):
            # Same absent-value rule as pricing: null or empty takes the default.
            kept[name] = "<unrecognized>"
    extra = rule.get("extra_credits_if_arg_contains")
    if isinstance(extra, Mapping) and extra.get("arg") in arguments:
        wanted = set(extra.get("values") or [])
        value = arguments.get(extra["arg"])
        items = value if isinstance(value, list) else [value]
        names = [item.get("type") if isinstance(item, Mapping) else item for item in items]
        kept[str(extra["arg"])] = sorted({n for n in names if isinstance(n, str) and n in wanted})
    for name in rule.get("unpriced_if_args") or []:
        if arguments.get(name) not in (None, "", [], {}):
            kept[str(name)] = "<present>"
    limit_arg = rule.get("result_limit_arg")
    if isinstance(limit_arg, str) and limit_arg in arguments:
        limit = arguments[limit_arg]
        kept[limit_arg] = limit if type(limit) is int and limit > 0 else "<unrecognized>"
    return kept


def _tavily_depth_overridden(default_parameters: str | None) -> bool:
    """Whether Tavily MCP will replace the observed search depth before billing."""

    if not default_parameters:
        return False
    try:
        defaults = json.loads(default_parameters)
    except ValueError:
        return False  # Tavily MCP ignores malformed DEFAULT_PARAMETERS.
    return isinstance(defaults, Mapping) and "search_depth" in defaults


# --------------------------------------------------------------------------
# Call records
# --------------------------------------------------------------------------


class CallMeter:
    """Pairs ``tools/call`` requests with responses and writes one record per call."""

    def __init__(
        self,
        *,
        provider_id: str,
        run_id: str,
        call_dir: Path,
        tariffs: Mapping[str, Mapping[str, Any]] | None = None,
        clock: Any = None,
    ) -> None:
        self._host = None
        self.provider_id = provider_id
        self.run_id = run_id
        self.call_dir = call_dir
        self.tools = dict((tariffs or {}).get(provider_id, {}))
        if provider_id == "exa" and os.environ.get("DEFAULT_SEARCH_TYPE"):
            # The pinned stdio server overrides the auto default from its env.
            from copy import deepcopy

            self.tools = deepcopy(self.tools)
            rule = self.tools.get("web_search_exa")
            if rule:
                mode = os.environ["DEFAULT_SEARCH_TYPE"]
                if mode in {"auto", "fast", "instant"}:
                    rule["pricing_tier"] = f"search:{mode}"
                else:
                    rule["unpriced_reason"] = "server_default_changes_price:DEFAULT_SEARCH_TYPE"
        if provider_id == "parallel-web" and os.environ.get("SEW_METER_PARALLEL_SEARCH_CONFIG"):
            from copy import deepcopy

            self.tools = deepcopy(self.tools)
            try:
                settings = json.loads(os.environ["SEW_METER_PARALLEL_SEARCH_CONFIG"])
            except ValueError:
                settings = {"mode": "<unrecognized>"}
            if not isinstance(settings, Mapping):
                settings = {"mode": "<unrecognized>"}
            settings = {k: v for k, v in settings.items() if k in {"mode", "max_results"}}
            rule = self.tools.get("web_search")
            if rule:
                selector = rule["pricing_tier"]
                mode = settings.get("mode")
                if isinstance(mode, str) and mode in selector["values"]:
                    selector["default"] = selector["values"][settings["mode"]]
                elif mode is not None:
                    rule["unpriced_reason"] = "connection_search_mode_unknown"
                if "max_results" in settings:
                    rule["default_result_limit"] = settings["max_results"]
            self._parallel_search_settings = settings
        else:
            self._parallel_search_settings = {}
        self._tavily_depth_overridden = provider_id == "tavily" and _tavily_depth_overridden(
            os.environ.get("DEFAULT_PARAMETERS")
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._pending: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._sequence = 0
        self._handshakes: dict[str, str] = {}
        self._pricing_missing_tools: set[str] = set()
        self._availability = availability_record(provider_id, run_id)
        self._availability.update(wrapper_engaged=True, observed=True, observation_reason=None)
        self._write_availability()
        # Frames that were not JSON; the relay still forwarded them unchanged.
        self.bad_frames = 0

    # The shared relay reads these hooks. SEW applies no MCP policy and needs
    # no run admission, so every call is registered and forwarded as-is.
    policy = None
    policy_loader = None
    admission_verifier = None

    def client_line(self, raw: bytes) -> None:
        message = self._decode(raw)
        if message is not None:
            self.client_message(message)

    def server_line(self, raw: bytes) -> None:
        message = self._decode(raw)
        if message is not None:
            self.server_message(message)

    def _decode(self, raw: bytes) -> Any:
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError):
            with self._lock:
                self.bad_frames += 1
            return None

    def client_message(self, message: Any) -> None:
        for item in message if isinstance(message, list) else [message]:
            if (
                isinstance(item, Mapping)
                and item.get("method") in {"initialize", "tools/list"}
                and "id" in item
            ):
                with self._lock:
                    method = item["method"]
                    self._handshakes[_key(item["id"])] = method
                    self._availability[
                        "initialize_requested" if method == "initialize" else "tools_list_requested"
                    ] = True
                    self._write_availability()
                continue
            if not isinstance(item, Mapping) or item.get("method") != "tools/call":
                continue
            if "id" not in item:
                continue
            params = item.get("params") if isinstance(item.get("params"), Mapping) else {}
            arguments = (
                params.get("arguments") if isinstance(params.get("arguments"), Mapping) else {}
            )
            with self._lock:
                self._pending[_key(item["id"])] = {
                    "tool": self._validated_tool(params.get("name")),
                    "arguments": dict(arguments),
                    "started_at": self._now(),
                }

    def server_message(self, message: Any) -> None:
        for item in message if isinstance(message, list) else [message]:
            if not isinstance(item, Mapping) or "id" not in item or "method" in item:
                continue
            with self._lock:
                method = self._handshakes.pop(_key(item["id"]), None)
                if method:
                    if "error" in item:
                        self._availability["observation_reason"] = "discovery_failed"
                    result = item.get("result")
                    ok = "error" not in item and isinstance(result, Mapping)
                    if method == "initialize" and ok:
                        self._availability["initialized"] = True
                    elif method == "tools/list" and ok and isinstance(result.get("tools"), list):
                        self._availability["tools_list_completed"] = True
                        self._availability["tool_count"] += len(result["tools"])
                        # Never publish unknown server-supplied tool names. Count
                        # uncovered tools so tool-surface drift cannot stay hidden.
                        tools = [t.get("name") for t in result["tools"] if isinstance(t, Mapping)]
                        self._pricing_missing_tools.update(
                            t for t in tools if isinstance(t, str) and not self.tools.get(t)
                        )
                        self._availability["pricing_missing_tools_n"] = len(
                            self._pricing_missing_tools
                        )
                    self._write_availability()
                pending = self._pending.pop(_key(item["id"]), None)
            if pending is not None:
                self._write(pending, item)

    def _write_availability(self) -> None:
        # Called under the observer lock (or before threads start). Persist at
        # every handshake boundary: SIGKILL may prevent a final flush.
        if not write_availability(self.call_dir, self._availability):
            _note_observation_failure(self.call_dir)

    def flush(self, error_class: str = "no_response") -> None:
        """Record calls that never got a response (server exit, harness kill)."""

        with self._lock:
            pending, self._pending = list(self._pending.values()), {}
        for call in pending:
            self._write(call, None, error_class=error_class)

    def _write(
        self,
        call: Mapping[str, Any],
        reply: Mapping[str, Any] | None,
        *,
        error_class: str | None = None,
    ) -> None:
        try:
            record = self.record(call, reply, error_class=error_class)
            from .host import get_host

            try:
                if self._host is None:
                    self._host = get_host()
                self._host.meter_provider_call(
                    provider_id=self.provider_id,
                    run_id=self.run_id,
                    tool=str(call.get("tool") or "unknown"),
                    arguments=dict(call.get("arguments") or {}),
                    reply=reply,
                )
            except Exception:
                pass  # Host metering must never interrupt local evidence or the relay.

            self.call_dir.mkdir(parents=True, exist_ok=True)
            fd, staging = tempfile.mkstemp(prefix=".call-", suffix=".json", dir=self.call_dir)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle, sort_keys=True)
                handle.write("\n")
            os.replace(staging, self.call_dir / f"{record['call_id']}.json")
        except Exception:  # noqa: BLE001 — fail-open: a record is never worth a broken relay
            pass

    def record(
        self,
        call: Mapping[str, Any],
        reply: Mapping[str, Any] | None,
        *,
        error_class: str | None = None,
    ) -> dict[str, Any]:
        ended_at = self._now()
        tool = self._validated_tool(call.get("tool"))
        arguments = call.get("arguments") if isinstance(call.get("arguments"), Mapping) else {}
        rule = self.tools.get(tool)
        effective_arguments = arguments
        connection_override = {}
        if tool == "web_search" and self.provider_id == "parallel-web":
            # Connection overrides are applied by the server after per-call args.
            effective_arguments = {**arguments, **self._parallel_search_settings}
            connection_override = pricing_arguments(rule, self._parallel_search_settings)
            if "max_results" in self._parallel_search_settings:
                count = self._parallel_search_settings["max_results"]
                connection_override["max_results"] = (
                    count if type(count) is int and count > 0 else "<unrecognized>"
                )
        known_keys = recognized_argument_keys(rule)
        argument_keys = sorted(key for key in arguments if key in known_keys)
        result = reply.get("result") if isinstance(reply, Mapping) else None
        error = reply.get("error") if isinstance(reply, Mapping) else None
        text = _result_text(result, error)
        is_error = error is not None or (
            isinstance(result, Mapping) and bool(result.get("isError"))
        )
        # @brave/brave-search-mcp-server@2.1.4: BraveAPI/index.js:105-115
        # throws on non-2xx; tools/web/index.js:25-38 emits this exact text
        # only after issueRequest returns. The summarizer uses the same text
        # for both empty summaries and caught failures, so it is excluded.
        billed_empty_result = (
            self.provider_id == "brave"
            and tool == "brave_web_search"
            and error is None
            and isinstance(result, Mapping)
            and result.get("isError") is True
            and result.get("content") == [{"type": "text", "text": BRAVE_WEB_EMPTY_RESULT}]
        )
        error_kind = None
        if is_error:
            error_kind = (
                "empty_result"
                if billed_empty_result
                else "http_error"
                if error is not None and re.search(r"\b[45]\d{2}\b", text)
                else "other"
            )
        if reply is None:
            status = "failed"
        elif is_error and any(marker in text.lower() for marker in RATE_LIMIT_MARKERS):
            status, error_class = "rate_limited", error_class or "rate_limited"
        elif is_error:
            status, error_class = "failed", error_class or "tool_error"
        else:
            status = "ok"
        core = _core()
        documents = core.response_documents(result) if core else []
        meter: dict[str, Any] = {"tool": tool, "via": "sew.mcp_meter"}
        if connection_override:
            meter["connection_override"] = connection_override
        spend = (
            core.measured_spend(documents, server=self.provider_id, arguments=effective_arguments)
            if status == "ok" and core
            else {}
        )
        if spend:
            meter["basis"] = "response"
        elif status == "ok" or billed_empty_result:
            if core is None:
                spend = {"unpriced_reason": CORE_UNAVAILABLE}
            elif self._tavily_depth_overridden and tool in ("tavily_search", "tavily-search"):
                spend = {"unpriced_reason": "server_default_changes_price:search_depth"}
            else:
                spend = core.tariff_spend(rule, effective_arguments, documents)
            reason = spend.pop("unpriced_reason", None)
            meter["basis"] = (
                "unpriced"
                if reason
                else "billed_empty_result"
                if billed_empty_result
                else "estimated_from_request"
                if "estimated_from_request"
                in (
                    spend.get("provider_units", {}).get("credits_source"),
                    spend.get("provider_units", {}).get("requests_source"),
                )
                else "tariff"
            )
            if reason:
                meter["unpriced_reason"] = reason
        else:
            meter["basis"] = "unpriced"
            meter["unpriced_reason"] = (
                "http_request_failed" if error_kind == "http_error" else f"call_{status}"
            )
        response: dict[str, Any] = {
            "is_error": is_error,
            "content_chars": len(text),
            "meter": meter,
            **spend,
        }
        if error_kind:
            response["error_kind"] = error_kind
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        started_at = str(call.get("started_at") or ended_at)
        call_id = hashlib.sha256(
            f"{self.run_id}\0{self.provider_id}\0{tool}\0{started_at}\0{sequence}".encode()
        ).hexdigest()[:24]
        record = {
            "schema_version": RECORD_SCHEMA_VERSION,
            "call_id": f"{self.provider_id}-{call_id}",
            "run_id": self.run_id,
            "provider_id": self.provider_id,
            "operation": tool,
            "status": status,
            "started_at": started_at,
            "ended_at": ended_at,
            "request": {
                "tool": tool,
                "argument_keys": argument_keys,
                "unrecognized_argument_key_count": len(arguments) - len(argument_keys),
                "pricing_arguments": pricing_arguments(rule, arguments),
            },
            "response": response,
            "normalized_source_refs": [],
            "retry_count": 0,
        }
        if error_class:
            record["error_class"] = error_class
        return record

    def _validated_tool(self, name: Any) -> str:
        # The tariff is checked-in operator data; MCP params.name is caller
        # supplied and must never become a published identifier or hash input.
        return name if isinstance(name, str) and name in self.tools else UNKNOWN_TOOL

    def _now(self) -> str:
        return self._clock().astimezone(UTC).isoformat().replace("+00:00", "Z")


def _key(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def availability_record(provider_id: str, run_id: str) -> dict[str, Any]:
    return dict(
        schema_version=1,
        wrapper_engaged=False,
        availability="unknown",
        observed=False,
        observation_reason="wrapper_not_engaged",
        provider_id=provider_id,
        run_id=run_id,
        initialize_requested=False,
        initialized=False,
        tools_list_requested=False,
        tools_list_completed=False,
        tool_count=0,
    )


def _note_observation_failure(call_dir: Path) -> None:
    """Keep evidence failures sticky without interrupting the vendor relay."""
    print(f"sew.mcp_meter: {OBSERVATION_FAILED}", file=sys.stderr, flush=True)
    try:
        marker = call_dir.parent / OBSERVATION_FAILURE_REF
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(OBSERVATION_FAILED + "\n", encoding="utf-8")
    except OSError:
        pass  # Stderr helps only when the harness forwards MCP child stderr.


def write_availability(call_dir: Path, record: Mapping[str, Any]) -> bool:
    """Secret-free handshake evidence, separate from billable call records."""
    staging = None
    try:
        call_dir.mkdir(parents=True, exist_ok=True)
        fd, staging = tempfile.mkstemp(prefix=".availability-", dir=call_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            snapshot = dict(record)
            observable = _record_meter_observed(
                record, record.get("provider_id"), record.get("run_id")
            )
            snapshot["availability"] = (
                (
                    "available"
                    if _handshake_available(record)
                    else "unavailable"
                    if record.get("observation_reason") == "discovery_failed"
                    or record.get("tools_list_completed")
                    else "unknown"
                )
                if observable
                else "unknown"
            )
            json.dump(snapshot, handle, sort_keys=True)
            handle.write("\n")
        os.replace(staging, call_dir / "availability.json")
        return True
    except OSError:
        return False  # Evidence failure must never break the transparent relay.
    finally:
        if staging:
            try:
                os.unlink(staging)
            except OSError:
                pass


def provider_available(
    run_dir: Path,
    provider_id: str,
    run_id: str,
    *,
    terminal: TerminalStatus | None = None,
    transcript: list[dict[str, Any]] | None = None,
    stderr: str = "",
) -> bool | None:
    """Prefer the saved live verdict; resolve legacy bundles from their evidence."""
    # Lazy import keeps the transparent child relay independent of harness deps.
    from .harness import STATUS_SUCCEEDED, provider_availability_eligible

    run = {}
    if terminal is None:
        try:
            run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if not isinstance(run, Mapping):
            run = {}
        if (
            run.get("provider_id", provider_id) != provider_id
            or run.get("run_id", run_id) != run_id
        ):
            return None
        if "provider_availability" in run:
            if run.get("provider_id") != provider_id or run.get("run_id") != run_id:
                return None
            return {"available": True, "unavailable": False}.get(str(run["provider_availability"]))
        if "status" in run and not provider_availability_eligible(
            run["status"], run.get("failure_category")
        ):
            return None
    elif not provider_availability_eligible(terminal.status, terminal.category):
        return None
    if transcript is None:
        transcript = _availability_transcript(run_dir)
    completed = transcript_provider_completed(run_dir, provider_id, transcript=transcript)
    try:
        record = json.loads((run_dir / AVAILABILITY_REF).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True if completed else None
    if (
        not isinstance(record, Mapping)
        or record.get("provider_id") != provider_id
        or record.get("run_id") != run_id
    ):
        return None
    # A completed matching call is stronger evidence than a stale snapshot,
    # including when the child could not overwrite the parent's launch record.
    if completed:
        return True
    if OBSERVATION_FAILED in stderr or (run_dir / OBSERVATION_FAILURE_REF).exists():
        return None
    try:
        if OBSERVATION_FAILED in (run_dir / "artifacts/harness-stderr.txt").read_text(
            encoding="utf-8"
        ):
            return None
    except OSError:
        pass
    if _record_meter_observed(record, provider_id, run_id):
        return _handshake_available(record)
    if record.get("wrapper_engaged") is True:
        harness_started = (terminal is not None and terminal.status == STATUS_SUCCEEDED) or any(
            event.get("event") in {"first_output_token"}
            for event in transcript
            if isinstance(event, Mapping)
        )
        if terminal is None:
            harness_started = harness_started or run.get("status") == STATUS_SUCCEEDED
        if harness_started:
            return _handshake_available(record)
    # Installing a wrapper precedes CLI boot. Without startup evidence its
    # missing discovery cannot turn a harness failure into provider failure.
    return None


def _availability_transcript(run_dir: Path) -> list[dict[str, Any]]:
    try:
        transcript = json.loads((run_dir / "artifacts/transcript.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return transcript if isinstance(transcript, list) else []


def _handshake_available(record: Mapping[str, Any]) -> bool:
    return (
        record.get("schema_version") == 1
        and record.get("initialized") is True
        and record.get("tools_list_requested") is True
        and record.get("tools_list_completed") is True
        and type(record.get("tool_count")) is int
        and record["tool_count"] > 0
    )


def meter_observed(run_dir: Path | None, provider_id: str, run_id: str) -> bool:
    if run_dir is None:
        return False
    try:
        record = json.loads((run_dir / AVAILABILITY_REF).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return _record_meter_observed(record, provider_id, run_id)


def _record_meter_observed(record: Any, provider_id: str, run_id: str) -> bool:
    return (
        isinstance(record, Mapping)
        and record.get("schema_version") == 1
        and record.get("provider_id") == provider_id
        and record.get("run_id") == run_id
        and (
            record.get("observed") is True
            or "observed" not in record
            and record.get("initialize_requested") is True
        )
    )


def transcript_provider_completed(
    run_dir: Path,
    provider_id: str,
    *,
    transcript: list[dict[str, Any]] | None = None,
) -> bool:
    """Require a completed matching Codex call or a paired Claude tool result."""
    from .arms import PROVIDER_SERVER_NAMES, _mcp_parts, _tool_call_entries

    if transcript is None:
        transcript = _availability_transcript(run_dir)
    server = PROVIDER_SERVER_NAMES.get(provider_id, provider_id)
    calls = set()
    results = set()
    for event in transcript:
        if not isinstance(event, Mapping):
            continue
        payload = event.get("harness_event", event)
        if not isinstance(payload, Mapping):
            continue
        for call_id, name in _tool_call_entries(event):
            parts = _mcp_parts(name)
            if parts and parts[0] == server:
                item = payload.get("item", {})
                if (
                    payload.get("type") == "item.completed"
                    and isinstance(item, Mapping)
                    and item.get("status") == "completed"
                ):
                    return True
                if call_id:
                    calls.add(call_id)
        message = payload.get("message", {})
        if isinstance(message, Mapping) and isinstance(message.get("content"), list):
            for block in message["content"]:
                if (
                    isinstance(block, Mapping)
                    and block.get("type") == "tool_result"
                    and block.get("is_error") is not True
                ):
                    call_id = block.get("tool_use_id")
                    if isinstance(call_id, str):
                        results.add(call_id)
    return bool(calls & results)


def _result_text(result: Any, error: Any) -> str:
    if isinstance(error, Mapping):
        return str(error.get("message") or "")
    parts = []
    if isinstance(result, Mapping):
        for item in result.get("content") or []:
            if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                parts.append(item["text"])
    return "\n".join(parts)


# --------------------------------------------------------------------------
# The stdio relay
# --------------------------------------------------------------------------


def _vendor_environment(core: SimpleNamespace | None) -> dict[str, str]:
    """Remove meter bootstrap imports before crossing into a vendor process."""
    env = dict(os.environ)
    injected = {Path(__file__).resolve().parents[1]}
    if core is not None:
        injected.add(Path(core.import_root).resolve())
    if "PYTHONPATH" in env:
        paths = [
            value
            for value in env["PYTHONPATH"].split(os.pathsep)
            if not value or Path(value).resolve() not in injected
        ]
        if paths:
            env["PYTHONPATH"] = os.pathsep.join(paths)
        else:
            env.pop("PYTHONPATH")
    return env


def serve(
    command: Sequence[str],
    meter: CallMeter,
    *,
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | None = None,
) -> int:
    """Run ``command`` as the vendor server and relay stdio through ``meter``.

    The shared relay registers each request before forwarding it, forwards
    each response before metering it, forwards signals and returns the
    server's exit code. Without the shared core the server runs unmetered.
    """

    core = _core()
    if core is None:
        return subprocess.call(
            list(command), stdin=stdin, stdout=stdout, env=_vendor_environment(core)
        )
    return core.serve(
        command, meter, stdin=stdin, stdout=stdout, env=_vendor_environment(core), inherit_env=False
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sew.mcp_meter",
        description="Relay a provider MCP server over stdio and meter its tool calls.",
    )
    parser.add_argument("--provider", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--call-dir", required=True, type=Path)
    parser.add_argument("--tariffs", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("the vendor MCP server command follows --")
    if _core() is None:
        record = availability_record(args.provider, args.run_id)
        record["observation_reason"] = CORE_UNAVAILABLE
        if not write_availability(args.call_dir, record):
            _note_observation_failure(args.call_dir)
        # The relay is the product: without the core, become the vendor server.
        os.execvpe(command[0], command, _vendor_environment(None))
    meter = CallMeter(
        provider_id=args.provider,
        run_id=args.run_id,
        call_dir=args.call_dir,
        tariffs=load_tariffs(args.tariffs),
    )
    return serve(command, meter)


def parallel_search_settings(server_config: Mapping[str, Any]) -> dict[str, Any]:
    """Only pricing selectors from documented MCP connection overrides.

    Never copy URLs, objectives, headers or credentials into meter evidence.
    URL query parameters override x-parallel-search-config fields.
    """
    from urllib.parse import parse_qs, urlsplit

    settings: dict[str, Any] = {}
    env = {**os.environ, **(server_config.get("env") or {})}

    def header(name: Any, raw: Any) -> None:
        if not isinstance(name, str) or name.lower() != "x-parallel-search-config":
            return
        if not isinstance(raw, str):
            settings["mode"] = "<unrecognized>"
            return
        raw = re.sub(r"\$\{([^}]+)\}", lambda m: str(env.get(m[1], m[0])), raw)
        try:
            value = json.loads(raw)
            if not isinstance(value, Mapping):
                raise ValueError("search config must be an object")
            if "mode" in value:
                mode = value["mode"]
                settings["mode"] = mode if isinstance(mode, str) else "<unrecognized>"
            advanced = value.get("advanced_settings")
            if isinstance(advanced, Mapping) and "max_results" in advanced:
                count = advanced["max_results"]
                settings["max_results"] = (
                    count if type(count) is int and count > 0 else "<unrecognized>"
                )
        except ValueError:
            settings["mode"] = "<unrecognized>"

    for key in ("headers", "http_headers"):
        headers = server_config.get(key)
        if isinstance(headers, Mapping):
            for name, raw in headers.items():
                header(name, raw)
    configured_header = server_config.get("header_from_env")
    if isinstance(configured_header, Mapping):
        header(configured_header.get("name"), configured_header.get("value"))
    args = server_config.get("args") or []
    for index, arg in enumerate(args):
        if not isinstance(arg, str):
            continue
        option, _, inline = arg.partition("=")
        if option not in {"--header", "--header-file"}:
            continue
        raw = inline if "=" in arg else args[index + 1] if index + 1 < len(args) else None
        if not isinstance(raw, str):
            continue
        if option == "--header":
            name, _, value = raw.partition(":")
            header(name.strip(), value.strip())
        else:
            try:
                lines = Path(raw).read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError):
                # An unreadable header file cannot establish a pricing mode.
                settings["mode"] = "<unrecognized>"
                continue
            for line in lines:
                name, _, value = line.partition(":")
                header(name.strip(), value.strip())
    candidates = [server_config.get("url"), *args]
    for url in candidates:
        if not isinstance(url, str):
            continue
        try:
            parsed = urlsplit(url)
            hostname = parsed.hostname
        except ValueError:
            continue
        if hostname != "search.parallel.ai":
            continue
        query = parse_qs(parsed.query)
        if "mode" in query:
            settings["mode"] = query["mode"][-1]
        if "advanced_settings.max_results" in query:
            raw_count = query["advanced_settings.max_results"][-1]
            try:
                count = int(raw_count) if raw_count.isascii() and raw_count.isdigit() else 0
            except ValueError:
                count = 0
            settings["max_results"] = count if count > 0 else "<unrecognized>"
    return {k: v for k, v in settings.items() if v is not None}


def metered_server_config(
    server_config: Mapping[str, Any],
    *,
    provider_id: str,
    run_id: str,
    call_dir: Path,
    python: str | None = None,
) -> dict[str, Any] | None:
    """The MCP server config that runs ``server_config`` behind the meter.

    Only stdio servers (a ``command``) can be wrapped; a remote (``url``)
    server is returned as ``None`` and stays unmetered, as does every server
    when the shared metering core is unavailable.
    """

    record = availability_record(provider_id, run_id)
    command = server_config.get("command")
    if not isinstance(command, str) or not command or server_config.get("url"):
        record["observation_reason"] = (
            "url_transport" if server_config.get("url") else "wrapper_not_engaged"
        )
        write_availability(call_dir, record)
        return None
    args = server_config.get("args") or []
    core = _core()
    if not isinstance(args, list) or core is None:
        record["observation_reason"] = CORE_UNAVAILABLE if core is None else "wrapper_not_engaged"
        write_availability(call_dir, record)
        return None
    # Installing the wrapper makes an absent launch meaningful evidence.
    record.update(wrapper_engaged=True, observation_reason="server_not_launched")
    write_availability(call_dir, record)
    lib = str(Path(__file__).resolve().parents[1])
    env = dict(server_config.get("env") or {})
    if provider_id == "parallel-web":
        settings = parallel_search_settings(server_config)
        if settings:
            env["SEW_METER_PARALLEL_SEARCH_CONFIG"] = json.dumps(settings)
    env["PYTHONPATH"] = os.pathsep.join([lib, core.import_root]) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    wrapped = dict(server_config)
    wrapped["command"] = python or sys.executable
    wrapped["args"] = [
        "-m",
        "sew.mcp_meter",
        "--provider",
        provider_id,
        "--run-id",
        run_id,
        "--call-dir",
        str(call_dir),
        "--",
        command,
        *[str(arg) for arg in args],
    ]
    wrapped["env"] = env
    return wrapped


if __name__ == "__main__":
    raise SystemExit(main())
