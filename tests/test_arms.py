from __future__ import annotations

import json
from pathlib import Path

import pytest

from sew.arms import (
    CODEX_DISABLED_FEATURES,
    PROVIDER_SERVER_NAMES,
    audit_transcript,
    contract_for,
    prepare_arm_spawn,
    provider_tool_calls,
)
from sew.harness import HarnessRunConfig, ProviderExposure, fixture_provider_exposure
from sew.live_harness import CodexProtocol
from sew.schema import SchemaError


def _provider_config(harness_id: str, provider_id: str) -> HarnessRunConfig:
    server = "parallel" if provider_id == "parallel-web" else provider_id
    return HarnessRunConfig(
        harness_id=harness_id,  # type: ignore[arg-type]
        provider_id=provider_id,
        task_id="current-fact-lookup-v1",
        mode="live",
        native_search_available=False,
        external_provider=ProviderExposure(
            provider_id=provider_id,
            tool_name=f"{server}.search",
            mcp_server_name=server,
            mcp_server_config={"command": "/usr/bin/true", "args": ["--fixture"]},
        ),
    )


@pytest.mark.parametrize("harness_id", ["claude-code", "codex"])
@pytest.mark.parametrize("provider_id", sorted(PROVIDER_SERVER_NAMES))
def test_provider_spawn_surface_contains_exactly_its_declared_mcp(
    tmp_path: Path, harness_id: str, provider_id: str
) -> None:
    config = _provider_config(harness_id, provider_id)
    surface = prepare_arm_spawn(config, tmp_path, {})
    server = "parallel" if provider_id == "parallel-web" else provider_id

    assert surface.contract.allowed_mcp_servers == (server,)
    text = surface.mcp_config_path.read_text(encoding="utf-8")
    for candidate in PROVIDER_SERVER_NAMES.values():
        assert (candidate in text) == (candidate == server)
    assert surface.contract.native_search is False
    if harness_id == "codex":
        import tomllib

        server_config = tomllib.loads(text)["mcp_servers"][server]
        assert server_config["startup_timeout_sec"] == 120
        assert server_config["required"] is True


@pytest.mark.parametrize("harness_id", ["claude-code", "codex"])
def test_no_search_arm_exposes_no_web_tool(tmp_path: Path, harness_id: str) -> None:
    config = HarnessRunConfig(
        harness_id=harness_id,  # type: ignore[arg-type]
        provider_id="no-search",
        task_id="current-fact-lookup-v1",
        mode="live",
        native_search_available=False,
    )
    surface = prepare_arm_spawn(config, tmp_path, {})

    assert surface.contract.allowed_mcp_servers == ()
    assert surface.contract.native_search is False
    assert not audit_transcript(
        surface.contract, [{"type": "tool_use", "name": "Read"}]
    ).contaminated
    assert audit_transcript(
        surface.contract, [{"type": "tool_use", "name": "WebSearch"}]
    ).contaminated


@pytest.mark.parametrize("harness_id", ["claude-code", "codex"])
def test_native_arm_exposes_no_provider_mcp(tmp_path: Path, harness_id: str) -> None:
    config = HarnessRunConfig(
        harness_id=harness_id,  # type: ignore[arg-type]
        provider_id="native",
        task_id="current-fact-lookup-v1",
        mode="live",
    )
    surface = prepare_arm_spawn(config, tmp_path, {})

    assert surface.contract.native_search is True
    assert surface.contract.allowed_mcp_servers == ()
    assert "exa" not in surface.mcp_config_path.read_text(encoding="utf-8")
    assert not audit_transcript(
        surface.contract, [{"type": "tool_use", "name": "web_search"}]
    ).contaminated


def test_every_external_provider_has_a_bakeoff_arm() -> None:
    from sew.schema import PROVIDERS

    assert set(PROVIDER_SERVER_NAMES) == set(PROVIDERS) - {
        "native",
        "no-search",
        "fixture",
        "floor",
        "ceiling",
    }


@pytest.mark.parametrize("provider_id", ["brave", "tavily", "perplexity"])
def test_new_provider_arms_admit_their_own_tools_and_flag_the_rest(provider_id: str) -> None:
    contract = contract_for(_provider_config("claude-code", provider_id))
    transcript = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": f"mcp__{provider_id}__web_search"},
                    {"type": "tool_use", "name": f"{provider_id}.search"},
                    {"type": "tool_use", "name": "mcp__exa__search"},
                    {
                        "type": "tool_use",
                        "name": "tavily.search" if provider_id != "tavily" else "brave.search",
                    },
                ]
            },
        }
    ]

    audit = audit_transcript(contract, transcript)
    assert audit.contaminated is True
    leaked = "tavily.search" if provider_id != "tavily" else "brave.search"
    assert audit.violations == ("mcp__exa__search", leaked)


def _bare_config(provider_id: str, *, native: bool) -> HarnessRunConfig:
    return HarnessRunConfig(
        harness_id="claude-code",
        provider_id=provider_id,
        task_id="current-fact-lookup-v1",
        mode="live",
        native_search_available=native,
    )


def _audit_one(contract, tool_name: str):
    return audit_transcript(
        contract,
        [{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": tool_name}]}}],
    )


@pytest.mark.parametrize(
    "tool_name", ["search", "search_web", "google_search", "WebFetch", "web_fetch"]
)
def test_generic_search_and_fetch_tools_are_web_tools(tool_name: str) -> None:
    # A tool named plain "search" used to pass the audit in every arm.
    no_search = contract_for(_bare_config("no-search", native=False))
    provider = contract_for(_provider_config("claude-code", "exa"))
    native = contract_for(_bare_config("native", native=True))

    assert _audit_one(no_search, tool_name).contaminated is True
    assert _audit_one(provider, tool_name).contaminated is True
    assert _audit_one(native, tool_name).contaminated is False


@pytest.mark.parametrize(
    "tool_name",
    ["ToolSearch", "Bash", "Read", "Grep", "file_search", "regex_search", "search_memory"],
)
def test_local_tools_never_count_as_web_access(tool_name: str) -> None:
    no_search = contract_for(_bare_config("no-search", native=False))
    assert _audit_one(no_search, tool_name).contaminated is False


def _fixture_provider_contract(provider_id: str):
    return contract_for(
        HarnessRunConfig(
            harness_id="claude-code",
            provider_id=provider_id,
            task_id="current-fact-lookup-v1",
            mode="live",
            native_search_available=False,
            external_provider=fixture_provider_exposure(provider_id),
        )
    )


def test_fixture_provider_tool_is_admitted_in_its_own_arm_only() -> None:
    exa = _fixture_provider_contract("exa")
    native = contract_for(_bare_config("native", native=True))

    assert _audit_one(exa, "sew_exa_search").contaminated is False
    assert _audit_one(exa, "sew_brave_search").contaminated is True
    # A provider's tool is never a harness-native web tool.
    assert _audit_one(native, "sew_exa_search").contaminated is True


@pytest.mark.parametrize(
    "tool_name", ["mcp__memory__read", "mcp__filesystem__list", "mcp.notes.get"]
)
def test_non_web_mcp_tools_are_outside_the_contract(tool_name: str) -> None:
    for contract in (
        contract_for(_bare_config("no-search", native=False)),
        contract_for(_bare_config("native", native=True)),
        _fixture_provider_contract("exa"),
    ):
        assert _audit_one(contract, tool_name).contaminated is False


@pytest.mark.parametrize(
    "tool_name",
    ["mcp__brave__brave_web_search", "mcp__tavily__extract", "mcp__custom__web_search"],
)
def test_web_or_provider_mcp_tools_outside_the_arm_are_contaminated(tool_name: str) -> None:
    assert _audit_one(_fixture_provider_contract("exa"), tool_name).contaminated is True
    assert (
        _audit_one(contract_for(_bare_config("no-search", native=False)), tool_name).contaminated
        is True
    )


@pytest.mark.parametrize("provider_id", ["no-search", "native", "exa"])
def test_claude_allowlist_never_grants_a_general_network_capable_tool(
    tmp_path: Path, provider_id: str
) -> None:
    # Bash could `curl` the web and bypass the arm, so the allowlist is strict.
    if provider_id == "exa":
        config = HarnessRunConfig(
            harness_id="claude-code",
            provider_id="exa",
            task_id="current-fact-lookup-v1",
            mode="live",
            native_search_available=False,
            external_provider=fixture_provider_exposure("exa"),
        )
    else:
        config = _bare_config(provider_id, native=provider_id == "native")
    surface = prepare_arm_spawn(config, tmp_path, {})
    allowed = surface.harness_args[surface.harness_args.index("--allowedTools") + 1]
    assert "bash" not in allowed.casefold()


@pytest.mark.parametrize("provider_id", ["no-search", "native", "exa"])
def test_codex_spawn_disables_network_capable_local_tools(tmp_path: Path, provider_id: str) -> None:
    if provider_id == "exa":
        config = _provider_config("codex", "exa")
    else:
        config = HarnessRunConfig(
            harness_id="codex",
            provider_id=provider_id,
            task_id="current-fact-lookup-v1",
            mode="live",
            native_search_available=provider_id == "native",
        )

    surface = prepare_arm_spawn(config, tmp_path, {})
    disabled = tuple(
        surface.harness_args[index + 1]
        for index, arg in enumerate(surface.harness_args[:-1])
        if arg == "--disable"
    )

    assert disabled == CODEX_DISABLED_FEATURES


def test_codex_caller_may_add_disable_hardening_flag(tmp_path: Path) -> None:
    config = HarnessRunConfig(
        harness_id="codex",
        provider_id="native",
        task_id="current-fact-lookup-v1",
        mode="live",
        harness_args=("--disable", "memories"),
    )

    surface = prepare_arm_spawn(config, tmp_path, {})

    assert surface.harness_args[:2] == ("--disable", "shell_tool")


@pytest.mark.parametrize("arg", ["-xc", "-ackey=value"])
def test_bundled_short_flags_cannot_smuggle_a_forbidden_option(tmp_path: Path, arg: str) -> None:
    config = HarnessRunConfig(
        harness_id="codex",
        provider_id="native",
        task_id="current-fact-lookup-v1",
        mode="live",
        harness_args=(arg,),
    )
    with pytest.raises(SchemaError, match="may not be overridden"):
        prepare_arm_spawn(config, tmp_path, {})


def test_out_of_arm_tool_call_is_contaminated() -> None:
    contract = contract_for(_provider_config("claude-code", "exa"))
    transcript = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "mcp__exa__search"},
                    {"type": "tool_use", "name": "mcp__firecrawl__firecrawl_search"},
                ]
            },
        }
    ]

    audit = audit_transcript(contract, transcript)
    assert audit.contaminated is True
    assert audit.violations == ("mcp__firecrawl__firecrawl_search",)


def test_codex_item_tool_call_is_contaminated() -> None:
    contract = contract_for(_provider_config("codex", "exa"))
    transcript = [
        {
            "type": "item.completed",
            "item": {"id": "tool_1", "type": "mcp_tool_call", "name": "firecrawl.search"},
        }
    ]

    audit = audit_transcript(contract, transcript)

    assert audit.contaminated is True
    assert audit.observed_tool_calls == ("firecrawl.search",)
    assert audit.violations == ("firecrawl.search",)


def test_embedded_tool_shaped_data_is_not_a_tool_call() -> None:
    contract = contract_for(_bare_config("no-search", native=False))
    transcript = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": "An API example follows.",
                        "example": {"type": "tool_use", "name": "WebSearch"},
                    }
                ]
            },
        },
        {
            "role": "harness",
            "harness_event": {
                "type": "item.completed",
                "item": {
                    "id": "item_0",
                    "type": "agent_message",
                    "text": "structured payload",
                    "payload": {"type": "web_search_call", "name": "web_search_call"},
                },
            },
        },
    ]

    audit = audit_transcript(contract, transcript)

    assert audit.contaminated is False
    assert audit.observed_tool_calls == ()


def test_provider_arm_fails_closed_without_resolvable_mcp_config() -> None:
    config = HarnessRunConfig(
        harness_id="codex",
        provider_id="exa",
        task_id="current-fact-lookup-v1",
        mode="live",
        native_search_available=False,
        external_provider=ProviderExposure(provider_id="exa", tool_name="exa.search"),
    )
    with pytest.raises(SchemaError, match="requires mcp_server_config"):
        contract_for(config)


def test_claude_no_search_uses_strict_empty_mcp_config(tmp_path: Path) -> None:
    config = HarnessRunConfig(
        harness_id="claude-code",
        provider_id="no-search",
        task_id="current-fact-lookup-v1",
        mode="live",
        native_search_available=False,
    )
    surface = prepare_arm_spawn(config, tmp_path, {})
    assert json.loads(surface.mcp_config_path.read_text()) == {"mcpServers": {}}
    assert surface.harness_args[-2:] == ("--allowedTools", "")
    assert surface.harness_args[surface.harness_args.index("--tools") + 1] == ""


def test_codex_native_flag_is_present_only_for_native_arm(tmp_path: Path) -> None:
    protocol = CodexProtocol()
    native = HarnessRunConfig(
        harness_id="codex", provider_id="native", task_id="current-fact-lookup-v1", mode="live"
    )
    no_search = HarnessRunConfig(
        harness_id="codex",
        provider_id="no-search",
        task_id="current-fact-lookup-v1",
        mode="live",
        native_search_available=False,
    )

    assert "--search" in protocol.argv("codex", native, tmp_path / "answer")
    assert "--search" not in protocol.argv("codex", no_search, tmp_path / "answer")


@pytest.mark.parametrize(
    ("harness_id", "arg"),
    [
        ("claude-code", "--mcp-config=malicious.json"),
        ("claude-code", "--allowedTools=Bash,WebSearch"),
        ("claude-code", "--tools=default"),
        ("claude-code", "--model=unrecorded-model"),
        ("claude-code", "--fallback-model=unrecorded-model"),
        ("codex", "--config=mcp_servers.evil.command=/bin/sh"),
        ("codex", "-cmcp_servers.evil.command=/bin/sh"),
        ("codex", "--model=unrecorded-model"),
        ("codex", "--profile=unrecorded-profile"),
    ],
)
def test_equals_and_attached_value_overrides_are_refused_too(
    tmp_path: Path, harness_id: str, arg: str
) -> None:
    config = HarnessRunConfig(
        harness_id=harness_id,  # type: ignore[arg-type]
        provider_id="native",
        task_id="current-fact-lookup-v1",
        mode="live",
        harness_args=(arg,),
    )
    with pytest.raises(SchemaError, match="may not be overridden"):
        prepare_arm_spawn(config, tmp_path, {})


@pytest.mark.parametrize(
    ("harness_id", "arg"), [("claude-code", "--mcp-config"), ("codex", "--config")]
)
def test_caller_cannot_override_arm_controlled_spawn_flags(
    tmp_path: Path, harness_id: str, arg: str
) -> None:
    config = HarnessRunConfig(
        harness_id=harness_id,  # type: ignore[arg-type]
        provider_id="native",
        task_id="current-fact-lookup-v1",
        mode="live",
        harness_args=(arg, "malicious"),
    )
    with pytest.raises(SchemaError, match="may not be overridden"):
        prepare_arm_spawn(config, tmp_path, {})


def _codex_mcp_call(server: str, tool: str, item_id: str = "item_1") -> dict[str, object]:
    # The shape codex exec --json really emits: no `name`, server and tool apart.
    return {
        "type": "item.completed",
        "item": {"id": item_id, "type": "mcp_tool_call", "server": server, "tool": tool},
    }


def test_codex_mcp_call_is_judged_by_its_server_not_its_bare_tool_name() -> None:
    exa = contract_for(_provider_config("codex", "exa"))
    firecrawl = contract_for(_provider_config("codex", "firecrawl"))
    own_call = _codex_mcp_call("exa", "web_search_exa")

    # Read bare, web_search_exa looks like a native web tool and failed its own arm.
    assert not audit_transcript(exa, [own_call]).contaminated
    leaked = audit_transcript(firecrawl, [own_call])
    assert leaked.violations == ("mcp__exa__web_search_exa",)
    # A leaked provider tool whose bare name is not web-shaped is caught too.
    assert audit_transcript(firecrawl, [_codex_mcp_call("exa", "crawling_exa")]).contaminated


def test_codex_native_web_search_item_is_a_web_tool_call() -> None:
    event = {"type": "item.completed", "item": {"id": "ws_1", "type": "web_search", "query": "q"}}
    native = contract_for(
        HarnessRunConfig(
            harness_id="codex",
            provider_id="native",
            task_id="current-fact-lookup-v1",
            mode="live",
        )
    )
    no_search = contract_for(
        HarnessRunConfig(
            harness_id="codex",
            provider_id="no-search",
            task_id="current-fact-lookup-v1",
            mode="live",
            native_search_available=False,
        )
    )

    assert not audit_transcript(native, [event]).contaminated
    assert audit_transcript(no_search, [event]).violations == ("web_search",)


def test_provider_tool_calls_are_search_calls_with_ids_for_dedupe() -> None:
    contract = contract_for(_provider_config("claude-code", "exa"))
    events = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "toolu_1", "name": "mcp__exa__web_search_exa"},
                    {"type": "tool_use", "id": "toolu_2", "name": "Read"},
                    {"type": "tool_use", "name": "WebSearch"},
                ]
            },
        },
        {"type": "item.started", "item": _codex_mcp_call("firecrawl", "scrape", "i_9")["item"]},
        _codex_mcp_call("firecrawl", "scrape", "i_9"),
    ]

    # Local tools are free; a leaked call still spent a provider call. The same
    # id twice is one call reported twice, which the caller counts once.
    assert provider_tool_calls(contract, events) == [
        ("toolu_1", "mcp__exa__web_search_exa"),
        (None, "WebSearch"),
        ("i_9", "mcp__firecrawl__scrape"),
        ("i_9", "mcp__firecrawl__scrape"),
    ]


@pytest.mark.parametrize(
    ("provider_id", "expected"),
    [("no-search", ""), ("native", "WebSearch,WebFetch,Read"), ("exa", "ToolSearch,Read")],
)
def test_claude_arms_restrict_the_builtin_tool_set(
    tmp_path: Path, provider_id: str, expected: str
) -> None:
    """--allowedTools only pre-approves; --tools is what removes WebFetch and Bash.

    Regression for the 2026-09-27 WSB trial, where provider and no-search arms
    ran WebFetch (and Bash) under a bare --allowedTools allowlist.
    """

    if provider_id == "exa":
        config = HarnessRunConfig(
            harness_id="claude-code",
            provider_id="exa",
            task_id="current-fact-lookup-v1",
            mode="live",
            native_search_available=False,
            external_provider=fixture_provider_exposure("exa"),
        )
    else:
        config = _bare_config(provider_id, native=provider_id == "native")
    surface = prepare_arm_spawn(config, tmp_path, {})
    tools = surface.harness_args[surface.harness_args.index("--tools") + 1]
    assert tools == expected
    names = {name.casefold() for name in tools.split(",") if name}
    assert "bash" not in names
    if provider_id != "native":
        assert not names & {"webfetch", "websearch"}
    # A result over Claude Code's MCP output limit is saved to a file the model
    # must Read; an arm that receives tool results needs it (spill probe).
    assert ("read" in names) == (provider_id != "no-search")


@pytest.mark.parametrize(
    ("provider_id", "mode"), [("no-search", "disabled"), ("exa", "disabled"), ("native", "live")]
)
def test_codex_arms_set_web_search_mode_explicitly(
    tmp_path: Path, provider_id: str, mode: str
) -> None:
    """codex-cli 0.157.0 serves web_search by default, even without --search.

    Regression for the 2026-09-27 WSB trial: the no-search, brave and tavily
    codex arms all ran native web_search and never used their MCP servers.
    """

    import tomllib

    if provider_id == "exa":
        config = _provider_config("codex", "exa")
    else:
        config = HarnessRunConfig(
            harness_id="codex",
            provider_id=provider_id,
            task_id="current-fact-lookup-v1",
            mode="live",
            native_search_available=provider_id == "native",
        )
    surface = prepare_arm_spawn(config, tmp_path, {})
    text = surface.mcp_config_path.read_text()
    assert text.splitlines()[0] == f'web_search = "{mode}"'
    parsed = tomllib.loads(text)
    assert parsed["web_search"] == mode
    if provider_id == "exa":
        assert set(parsed["mcp_servers"]) == {"exa"}
        # Without pre-approval `codex exec` (approval policy never) fails every
        # MCP call before it reaches the arm's server.
        assert parsed["mcp_servers"]["exa"]["default_tools_approval_mode"] == "approve"


def test_codex_arms_disable_every_command_and_connector_surface() -> None:
    required = {
        "shell_tool",
        "unified_exec",
        "apps",
        "plugins",
        "remote_plugin",
        "skill_mcp_dependency_install",
        "tool_suggest",
        "browser_use",
        "computer_use",
    }
    assert required <= set(CODEX_DISABLED_FEATURES)


def test_codex_command_execution_is_recorded_but_not_contamination() -> None:
    config = HarnessRunConfig(
        harness_id="codex",
        provider_id="no-search",
        task_id="current-fact-lookup-v1",
        mode="live",
        native_search_available=False,
    )
    contract = contract_for(config)
    event = {
        "type": "item.completed",
        "item": {"id": "cmd_1", "type": "command_execution", "command": "ls"},
    }
    audit = audit_transcript(contract, [event])
    assert audit.observed_tool_calls == ("command_execution",)
    assert audit.violations == ()


@pytest.mark.parametrize(
    ("provider_id", "expected"),
    [("no-search", ""), ("native", "WebSearch,WebFetch"), ("exa", "mcp__exa__*")],
)
def test_claude_arms_preapprove_every_web_tool_they_expose(
    tmp_path: Path, provider_id: str, expected: str
) -> None:
    """Headless Claude Code refuses an exposed tool that is not pre-approved.

    Regression for the native arm, which exposed WebFetch through --tools but
    pre-approved only WebSearch, so WebFetch calls were refused ("you haven't
    granted it yet") in the 2026-10-03 GAP battery and most bakeoff cells.
    """

    if provider_id == "exa":
        config = HarnessRunConfig(
            harness_id="claude-code",
            provider_id="exa",
            task_id="current-fact-lookup-v1",
            mode="live",
            native_search_available=False,
            external_provider=fixture_provider_exposure("exa"),
        )
    else:
        config = _bare_config(provider_id, native=provider_id == "native")
    surface = prepare_arm_spawn(config, tmp_path, {})
    allowed = surface.harness_args[surface.harness_args.index("--allowedTools") + 1]
    assert allowed == expected
    exposed = surface.harness_args[surface.harness_args.index("--tools") + 1].split(",")
    web_tools = {name for name in exposed if name in {"WebSearch", "WebFetch"}}
    assert web_tools <= set(allowed.split(","))
