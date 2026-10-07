"""Per-cell search-arm isolation for the Web Search Bakeoff.

The contract has two independent fences:

* :func:`prepare_arm_spawn` gives the child a fresh configuration containing
  exactly the declared provider MCP, native search, or neither.
* :func:`audit_transcript` reads the captured protocol events and rejects any
  web tool call outside that declaration.

The second fence is intentional: launch configuration is not evidence of what
the model actually reached, and a plausible answer from a leaked tool must
never be scored.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from . import harnesses
from .schema import SchemaError

ArmKind = Literal["provider", "native", "no-search", "floor", "ceiling"]

# Every external provider the workbench measures gets a bakeoff arm. Brave,
# Tavily and Perplexity joined after the WSB spec was written; leaving them out
# here would silently drop them from the live bakeoff.
PROVIDER_SERVER_NAMES = {
    "exa": "exa",
    "parallel-web": "parallel",
    "firecrawl": "firecrawl",
    "brave": "brave",
    "tavily": "tavily",
    "perplexity": "perplexity",
}
NATIVE_TOOLS = {"search", "websearch", "web_search", "web.search", "web-search"}
# Search is a local operation in names like file_search or search_memory. Only
# exact native web tools, names that pair search/fetch with a web token, and
# named web-search engines count as network-capable native tools.
WEB_FETCH_TOOLS = {"webfetch", "web_fetch", "web.fetch", "web-fetch"}
WEB_SEARCH_ENGINES = {"bing", "brave", "duckduckgo", "google", "kagi"}
# Built-in harness tools whose names contain a web stem but never reach the
# network. Claude Code's ToolSearch only loads deferred tool schemas.
LOCAL_TOOLS = frozenset({"toolsearch"})
# Every codex feature that can run commands, reach the network, or add tools
# from outside the arm's own config. codex-cli 0.157.0 enables all of these by
# default (`codex features list`); unified_exec is a second command tool that
# shell_tool does not cover, and apps/plugins pull the account's connectors.
CODEX_DISABLED_FEATURES = (
    "shell_tool",
    "unified_exec",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "computer_use",
    "in_app_browser",
    "in_app_local_automation",
    "apps",
    "plugins",
    "remote_plugin",
    "skill_mcp_dependency_install",
    "tool_suggest",
)


@dataclass(frozen=True)
class ArmContract:
    kind: ArmKind
    harness_id: str
    provider_id: str | None = None
    mcp_server_name: str | None = None
    mcp_server_config: Mapping[str, Any] | None = None
    # The provider exposure's declared tool name (e.g. the fixture's
    # "sew_exa_search"). Always admitted in its own provider arm.
    tool_name: str | None = None
    workspace_profile: bool = False
    oracle_excerpt_sha256: str | None = None

    @property
    def allowed_mcp_servers(self) -> tuple[str, ...]:
        return (self.mcp_server_name,) if self.mcp_server_name else ()

    @property
    def native_search(self) -> bool:
        return self.kind == "native"

    def as_record(self) -> dict[str, Any]:
        return {
            **({"workspace_profile": True} if self.workspace_profile else {}),
            **(
                {"reference_arm": self.kind, "oracle_excerpt_sha256": self.oracle_excerpt_sha256}
                if self.kind in {"floor", "ceiling"}
                else {}
            ),
            "kind": self.kind,
            "provider_id": self.provider_id,
            "allowed_mcp_servers": list(self.allowed_mcp_servers),
            "native_search": self.native_search,
        }


@dataclass(frozen=True)
class SpawnSurface:
    contract: ArmContract
    harness_args: tuple[str, ...]
    env: Mapping[str, str]
    mcp_config_path: Path


@dataclass(frozen=True)
class TranscriptAudit:
    contaminated: bool
    observed_tool_calls: tuple[str, ...]
    violations: tuple[str, ...]
    denied_network_attempts: int = 0
    config_neutralized_network_attempts: int = 0
    new_package_attempts: int = 0


def contract_for(config: Any) -> ArmContract:
    """Resolve one unambiguous arm from a ``HarnessRunConfig``-shaped value."""

    workspace = getattr(config, "workspace_profile", False)
    if config.provider_id in {"floor", "ceiling"}:
        from .gap.reference import resolve_gap_task

        if config.external_provider is not None or config.native_search_available:
            raise SchemaError("reference arm must expose no search tools")
        _, task = resolve_gap_task(config)
        return ArmContract(
            config.provider_id,
            config.harness_id,
            workspace_profile=workspace,
            oracle_excerpt_sha256=task["oracle"]["sha256"],
        )
    if config.provider_id == "no-search":
        if config.external_provider is not None or config.native_search_available:
            raise SchemaError("no-search arm must disable native search and expose no provider MCP")
        return ArmContract("no-search", config.harness_id, workspace_profile=workspace)
    if config.provider_id == "native":
        if config.external_provider is not None:
            raise SchemaError("native arm must expose no provider MCP")
        if not config.native_search_available:
            raise SchemaError("native arm requires native_search_available=true")
        return ArmContract("native", config.harness_id, workspace_profile=workspace)
    if config.provider_id not in PROVIDER_SERVER_NAMES:
        raise SchemaError(
            f"bakeoff provider arm must be one of {', '.join(sorted(PROVIDER_SERVER_NAMES))}"
        )
    exposure = config.external_provider
    if exposure is None or exposure.provider_id != config.provider_id:
        raise SchemaError("provider arm requires its matching provider exposure")
    if config.native_search_available:
        raise SchemaError("provider arm must disable native search")
    server = exposure.mcp_server_name or PROVIDER_SERVER_NAMES[config.provider_id]
    if not isinstance(exposure.mcp_server_config, Mapping) or not exposure.mcp_server_config:
        raise SchemaError(f"provider arm {config.provider_id!r} requires mcp_server_config")
    return ArmContract(
        "provider",
        config.harness_id,
        provider_id=config.provider_id,
        mcp_server_name=server,
        mcp_server_config=exposure.mcp_server_config,
        tool_name=exposure.tool_name,
        workspace_profile=workspace,
    )


def prepare_arm_spawn(
    config: Any, scratch: Path, source_env: Mapping[str, str], *, harness_auth: str = "account"
) -> SpawnSurface:
    """Materialize a fail-closed, per-cell harness tool configuration."""

    contract = contract_for(config)
    spec = harnesses.find(config.harness_id)
    if spec is None or spec.arm_spawn is None:
        raise SchemaError(f"unsupported live harness for arm isolation: {config.harness_id!r}")
    _reject_tool_overrides(config, spec.forbidden_flags)
    server_config = dict(contract.mcp_server_config or {})
    header = server_config.pop("header_from_env", None)
    if header is not None:
        if (
            not isinstance(header, Mapping)
            or not isinstance(header.get("name"), str)
            or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", header["name"])
            or not isinstance(header.get("value"), str)
            or any(char in header["value"] for char in "\r\n\0")
            or not server_config.get("command")
            or server_config.get("url")
            or not isinstance(server_config.get("args", []), list)
        ):
            raise SchemaError(
                "header_from_env requires a stdio command, args list, "
                "header name and single-line value"
            )
        secret_dir = scratch / ".secrets"
        secret_dir.mkdir(mode=0o700)
        header_path = secret_dir / "provider-header.txt"
        fd = os.open(header_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"{header['name']}: {header['value']}\n")
        server_config["args"] = [*server_config.get("args", []), "--header-file", str(header_path)]
    return spec.load("arm_spawn")(
        config, contract, server_config, scratch, source_env, harness_auth=harness_auth
    )


def claude_code_arm_spawn(
    config: Any,
    contract: ArmContract,
    server_config: dict[str, Any],
    scratch: Path,
    source_env: Mapping[str, str],
    *,
    harness_auth: str = "account",
) -> SpawnSurface:
    """Claude Code's arm surface: a strict MCP config and the arm's built-in tools."""

    workspace = contract.workspace_profile
    path = scratch / "claude-mcp.json"
    servers = {contract.mcp_server_name: server_config} if contract.kind == "provider" else {}
    path.write_text(
        json.dumps({"mcpServers": servers}, sort_keys=True) + "\n", encoding="utf-8"
    )
    # Every bakeoff task is web retrieval with a final-answer deliverable,
    # and a general tool such as Bash could `curl` the web and bypass the
    # arm entirely. `--allowedTools` only pre-approves tools: in headless
    # `--print` mode Claude Code 2.1.x still ran WebFetch and Bash in
    # provider and no-search arms (WSB trial, 2026-09-27). `--tools` is
    # what restricts the built-in set, so each arm names exactly the
    # built-ins it may use. MCP tools come only from --mcp-config.
    #   native     WebSearch + WebFetch (the harness's own web tools) + Read
    #   provider   ToolSearch, so a deferred MCP schema can still load, + Read
    #   no-search  nothing
    # Read is local, never a web tool. Claude Code saves a tool result over
    # its output limit (MAX_MCP_OUTPUT_TOKENS, 25k by default) to a file and
    # tells the model to Read it; without Read a large crawl or extract is
    # unreadable (live probe, 2026-09-27: CONTENT_UNREADABLE without Read,
    # the right answer with it), which would bias the arm comparison.
    tools = (
        "WebSearch,WebFetch,Read"
        if contract.kind == "native"
        else "ToolSearch,Read"
        if contract.kind == "provider"
        else ""
    )
    # Every built-in an arm may use must also be pre-approved: in headless
    # `--print` mode an unapproved tool is refused with "Claude requested
    # permissions to use WebFetch, but you haven't granted it yet". The
    # native arm pre-approved only WebSearch until 2026-10-06, so its
    # WebFetch calls were refused in every GAP battery cell and in 40 of 54
    # bakeoff cells (published as a correction in both reports).
    allowed = (
        "WebSearch,WebFetch"
        if contract.kind == "native"
        else f"mcp__{contract.mcp_server_name}__*"
        if contract.kind == "provider"
        else ""
    )
    extra_args = ()
    if workspace:
        tools = "Read,Edit,Write,Bash" + (
            ",WebSearch,WebFetch"
            if contract.kind == "native"
            else ",ToolSearch"
            if contract.kind == "provider"
            else ""
        )
        search_allowed = "WebSearch,WebFetch" if contract.kind == "native" else allowed
        allowed = "Read,Edit,Write,Bash" + ("," + search_allowed if search_allowed else "")
        settings = scratch / "claude-workspace-settings.json"
        settings.write_text(
            json.dumps(
                {
                    "sandbox": {
                        "enabled": True,
                        "failIfUnavailable": True,
                        "autoAllowBashIfSandboxed": True,
                        "allowUnsandboxedCommands": False,
                        "excludedCommands": [],
                        "network": {
                            "allowedDomains": [],
                            "deniedDomains": ["*"],
                            "strictAllowlist": True,
                            "allowLocalBinding": False,
                            "allowAllUnixSockets": False,
                        },
                    },
                    "disableAllHooks": True,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        extra_args = (
            "--settings",
            str(settings),
            "--setting-sources",
            "",
            "--permission-mode",
            "acceptEdits",
        )
    isolated_env = {}
    if harness_auth == "litellm":
        config_dir = scratch / "claude-config"
        config_dir.mkdir(mode=0o700)
        isolated_env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    return SpawnSurface(
        contract,
        (
            "--mcp-config",
            str(path),
            "--strict-mcp-config",
            "--tools",
            tools,
            "--allowedTools",
            allowed,
        )
        + extra_args,
        isolated_env,
        path,
    )



def codex_arm_spawn(
    config: Any,
    contract: ArmContract,
    server_config: dict[str, Any],
    scratch: Path,
    source_env: Mapping[str, str],
    *,
    harness_auth: str = "account",
) -> SpawnSurface:
    """Codex's arm surface: an isolated CODEX_HOME and every off-arm feature disabled."""

    workspace = contract.workspace_profile
    codex_home = scratch / "codex-home"
    codex_home.mkdir(mode=0o700)
    source_home = Path(source_env.get("CODEX_HOME", Path(source_env.get("HOME", "")) / ".codex"))
    if harness_auth == "account":
        # Explicit account mode preserves the historical login behavior.
        for name in ("auth.json", ".credentials.json"):
            source = source_home / name
            if source.is_file():
                shutil.copy2(source, codex_home / name)
    path = codex_home / "config.toml"
    # codex-cli 0.157.0 serves its native `web_search` tool by default (cached
    # mode) whether or not `--search` is passed: in the 2026-09-27 WSB trial the
    # no-search, brave and tavily codex arms all ran web_search and never used
    # their MCP servers. The mode is therefore set explicitly for every arm.
    # A top-level key must precede any [table], so it is written first.
    lines: list[str] = [
        'web_search = "live"' if contract.kind == "native" else 'web_search = "disabled"'
    ]
    if workspace:
        lines.extend(
            ['approval_policy = "never"', "[sandbox_workspace_write]", "network_access = false"]
        )
        lines.append("[shell_environment_policy.set]")
        lines.extend(
            f"{key} = {json.dumps(value)}"
            for key, value in config.env.items()
            if key.startswith("PIP_") or key in {"PATH", "VIRTUAL_ENV"}
        )
    if contract.kind == "provider":
        lines.extend(_toml_mcp_server(contract.mcp_server_name or "", server_config))
    path.write_text("\n".join(lines), encoding="utf-8")
    path.chmod(0o600)
    isolation_args = tuple(
        arg
        for feature in CODEX_DISABLED_FEATURES
        if not (workspace and feature in {"shell_tool", "unified_exec"})
        for arg in ("--disable", feature)
    )
    return SpawnSurface(contract, isolation_args, {"CODEX_HOME": str(codex_home)}, path)


def audit_transcript(
    contract: ArmContract,
    transcript: Any,
    *,
    cwd: Path | None = None,
    forbidden_paths=(),
    cell_env=None,
    new_packages=(),
) -> TranscriptAudit:
    calls = tuple(_tool_calls(transcript))
    violations = tuple(name for name in calls if not _allowed(contract, name))
    denied_attempts = set()
    neutralized_attempts = set()
    new_package_attempts = set()
    if contract.workspace_profile or forbidden_paths:
        from .gap.workspace import audit_workspace_calls

        violations += tuple(
            audit_workspace_calls(
                transcript,
                cwd=cwd,
                forbidden_paths=forbidden_paths,
                denied_attempts=denied_attempts,
                cell_env=cell_env,
                neutralized_attempts=neutralized_attempts,
                new_packages=new_packages,
                new_package_attempts=new_package_attempts,
            )
        )
    return TranscriptAudit(
        bool(violations),
        calls,
        violations,
        len(denied_attempts),
        len(neutralized_attempts),
        len(new_package_attempts),
    )


def provider_tool_calls(contract: ArmContract, events: Any) -> list[tuple[str | None, str]]:
    """``(call_id, tool_name)`` for every call in ``events`` that reaches search.

    This is what a cell's provider-call budget counts: calls to the arm's own
    provider server, to any other search provider, or to a web tool. Local
    tools (Read, Grep) are free. A leaked call counts too; it spent a provider
    call even though the audit will also fail the cell as contaminated.
    The id lets a caller count a call once when a harness reports it twice
    (Codex emits ``item.started`` and ``item.completed`` for one call).
    """

    return [
        (call_id, name)
        for call_id, name in _tool_call_entries(events)
        if _reaches_search(contract, name)
    ]


def _reaches_search(contract: ArmContract, tool_name: str) -> bool:
    normalized = tool_name.casefold()
    if contract.tool_name and normalized == contract.tool_name.casefold():
        return True
    mcp = _mcp_parts(normalized)
    if mcp is not None:
        mcp_server, mcp_tool = mcp
        own_server = (contract.mcp_server_name or "").casefold()
        return (
            (bool(own_server) and mcp_server == own_server)
            or mcp_server in _PROVIDER_SERVER_VALUES
            or _is_web_tool(mcp_server)
            or _is_web_tool(mcp_tool)
        )
    return _provider_server_of(normalized) is not None or _is_web_tool(normalized)


def _allowed(contract: ArmContract, tool_name: str) -> bool:
    normalized = tool_name.casefold()
    server = (contract.mcp_server_name or "").casefold()
    if contract.kind == "provider" and normalized == (contract.tool_name or "").casefold():
        return True
    mcp = _mcp_parts(normalized)
    if mcp is not None:
        mcp_server, mcp_tool = mcp
        if contract.kind == "provider" and mcp_server == server:
            return True
        # Another MCP server is contamination only when it reaches the web: a
        # search-provider server, or a tool that is itself a web tool. A
        # non-web MCP tool (memory, filesystem) is outside this contract.
        return not (
            mcp_server in _PROVIDER_SERVER_VALUES
            or _is_web_tool(mcp_server)
            or _is_web_tool(mcp_tool)
        )
    provider_server = _provider_server_of(normalized)
    if provider_server is not None:
        return contract.kind == "provider" and provider_server == server
    if _is_web_tool(normalized):
        return contract.kind == "native"
    # Non-web tools are outside this contract; the harness sandbox/tool policy
    # may allow them without changing which search arm a cell belongs to.
    return True


_PROVIDER_SERVER_VALUES = frozenset(PROVIDER_SERVER_NAMES.values())


def _mcp_parts(name: str) -> tuple[str, str] | None:
    """(server, tool) for an MCP tool name in any harness spelling, else None."""

    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        return (parts[1], parts[2] if len(parts) > 2 else "")
    if name.startswith(("mcp.", "mcp_", "mcp:", "mcp/")):
        parts = re.split(r"[._:/]", name, maxsplit=2)
        return (parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else "")
    return None


def _provider_server_of(name: str) -> str | None:
    """The provider server a non-MCP tool name belongs to, if any.

    Covers codex-style ``<server>.<tool>`` names and the fixture exposure's
    ``sew_<provider>_<tool>`` names, so a provider's tool counts only in that
    provider's arm and never as a harness-native web tool.
    """

    for provider_id, provider_server in PROVIDER_SERVER_NAMES.items():
        fixture_prefix = f"sew_{provider_id.replace('-', '_')}_"
        if name.startswith(f"{provider_server}.") or name.startswith(fixture_prefix):
            return provider_server
    return None


def _is_web_tool(name: str) -> bool:
    if name in LOCAL_TOOLS:
        return False
    if name in NATIVE_TOOLS or name in WEB_FETCH_TOOLS:
        return True
    tokens = tuple(token for token in re.split(r"[^a-z0-9]+", name) if token)
    if "web" in tokens and ("search" in tokens or "fetch" in tokens):
        return True
    return bool(tokens) and tokens[-1] == "search" and tokens[0] in WEB_SEARCH_ENGINES


_TOOL_CALL_EVENT_TYPES = frozenset(
    {
        "tool_use",
        "tool_call",
        "function_call",
        "mcp_tool_call",
        "web_search_call",
        # codex exec --json reports a native web search as its own item type.
        "web_search",
        "command_execution",
    }
)
_UNNAMED_WEB_SEARCH_TYPES = frozenset({"web_search_call", "web_search"})
# Unnamed items that are recorded under their own type. codex reports a shell
# command as `command_execution`; it is not a web tool, so it never marks a cell
# contaminated, but it is recorded so a transcript shows every command an arm ran.
_UNNAMED_TOOL_TYPES = _UNNAMED_WEB_SEARCH_TYPES | {"command_execution"}


def _tool_calls(value: Any) -> list[str]:
    return [name for _, name in _tool_call_entries(value)]


def _tool_call_entries(value: Any) -> list[tuple[str | None, str]]:
    found: list[tuple[str | None, str]] = []
    events = value if isinstance(value, list | tuple) else (value,)
    for event in events:
        if not isinstance(event, Mapping):
            continue
        found.extend(_tool_call_entry(event))
        found.extend(_protocol_tool_calls(event))
    return found


def _protocol_tool_calls(event: Mapping[str, Any]) -> list[tuple[str | None, str]]:
    protocol_event = event.get("harness_event")
    if isinstance(protocol_event, Mapping):
        return _protocol_tool_calls(protocol_event)

    found: list[tuple[str | None, str]] = []
    message = event.get("message")
    if isinstance(message, Mapping):
        content = message.get("content")
        if isinstance(content, list | tuple):
            for block in content:
                if isinstance(block, Mapping):
                    found.extend(_tool_call_entry(block))

    item = event.get("item")
    if isinstance(item, Mapping):
        found.extend(_tool_call_entry(item))
    return found


def _tool_call_entry(value: Mapping[str, Any]) -> list[tuple[str | None, str]]:
    kind = str(value.get("type") or "").casefold()
    if kind not in _TOOL_CALL_EVENT_TYPES:
        return []
    name = value.get("name") or value.get("tool")
    if name is None and kind in _UNNAMED_TOOL_TYPES:
        name = kind
    function = value.get("function")
    if name is None and isinstance(function, Mapping):
        name = function.get("name")
    if not isinstance(name, str) or not name:
        return []
    server = value.get("server")
    if isinstance(server, str) and server and _mcp_parts(name.casefold()) is None:
        # codex exec --json names an MCP call by `server` and `tool` separately
        # ({"type": "mcp_tool_call", "server": "exa", "tool": "web_search_exa"}).
        # The bare tool name would read as a native web tool, so the arm's own
        # provider call would fail the cell as contaminated.
        name = f"mcp__{server}__{name}"
    call_id = value.get("id") or value.get("call_id")
    return [(call_id if isinstance(call_id, str) and call_id else None, name)]


def _reject_tool_overrides(config: Any, harness_forbidden: frozenset[str]) -> None:
    # The harness's own arm-owned flags (its registry entry), plus every flag
    # that could widen a GAP workspace's sandbox on any harness.
    forbidden = set(harness_forbidden)
    if getattr(config, "workspace_profile", False):
        forbidden |= {
            "--settings",
            "--setting-sources",
            "--permission-mode",
            "--add-dir",
            "--dangerously-skip-permissions",
            "--allow-dangerously-skip-permissions",
            "--sandbox",
            "-s",
            "--full-auto",
            "--yolo",
            "--dangerously-bypass-approvals-and-sandbox",
            "--ask-for-approval",
            "-a",
            "--permission-profile",
            "-P",
            "--cd",
            "-C",
            "--plugin-dir",
        }
    conflicts = sorted({arg for arg in config.harness_args if _flag_names(arg) & forbidden})
    if conflicts:
        raise SchemaError(f"arm-controlled harness arguments may not be overridden: {conflicts}")


def _flag_names(arg: str) -> set[str]:
    """Every option an argument could set, whatever syntax carries its value.

    ``--mcp-config=x.json`` sets ``--mcp-config``. A single-dash argument is
    read as a bundle: ``-ckey=v`` and ``-xc`` both could set ``-c``. This
    deliberately fails closed: an attached short value such as ``-mcodex`` is
    also refused if it contains a forbidden letter, and the caller should
    spell it with a long option instead.
    """

    if arg.startswith("--"):
        return {arg.split("=", 1)[0]}
    if arg.startswith("-") and len(arg) > 2:
        return {f"-{ch}" for ch in arg[1:] if ch.isalpha()}
    return {arg}


def _toml_mcp_server(name: str, config: Mapping[str, Any]) -> list[str]:
    if not name or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise SchemaError(f"invalid MCP server name: {name!r}")
    allowed = {"command", "args", "url", "env", "headers", "startup_timeout_sec"}
    unknown = set(config) - allowed
    if unknown:
        raise SchemaError(f"unsupported MCP server config keys: {sorted(unknown)}")
    lines = [f"[mcp_servers.{name}]"]
    startup_timeout = config.get("startup_timeout_sec", 120)
    if type(startup_timeout) not in (int, float) or not 0 < startup_timeout < float("inf"):
        raise SchemaError("MCP startup_timeout_sec must be a positive finite number")
    lines.append(f"startup_timeout_sec = {startup_timeout}")
    # A benchmark arm must wait for its only provider, including Codex versions
    # with a shorter shared grace period for optional MCP servers.
    lines.append("required = true")
    for key in ("command", "url"):
        if key in config:
            lines.append(f"{key} = {json.dumps(str(config[key]))}")
    if "args" in config:
        if not isinstance(config["args"], list | tuple):
            raise SchemaError("MCP server args must be a list")
        lines.append(f"args = {json.dumps([str(v) for v in config['args']])}")
    # The arm's own server is the only MCP surface it has, so its tools are
    # pre-approved. `codex exec` runs with approval policy `never`, and without
    # this every call fails ("MCP tool call requires approval, but approval
    # policy is never") before it reaches the server: in the 2026-09-27 trial
    # the codex brave and tavily arms never searched and were never metered.
    lines.append('default_tools_approval_mode = "approve"')
    for table in ("env", "headers"):
        if table not in config:
            continue
        values = config[table]
        if not isinstance(values, Mapping):
            raise SchemaError(f"MCP server {table} must be an object")
        lines.append(f"[mcp_servers.{name}.{table}]")
        lines.extend(f"{json.dumps(str(k))} = {json.dumps(str(v))}" for k, v in values.items())
    return lines + [""]
