"""Opencode >=1.17.3: isolated LiteLLM search cells and run JSON events.

Event vocabulary verified against v1.17.3 packages/opencode/src/cli/cmd/run.ts.
Native webfetch/websearch are client-side tools; provider arms disable both.
"""
from __future__ import annotations

import json

from .registry import HarnessSpec

PROVIDER = "searchlight-litellm"
MIN_VERSION = "1.17.3"
# Built-in tools a GAP code cell may use. Search cells keep every one off.
WORKSPACE_TOOLS = ("bash", "read", "write", "edit", "glob", "grep", "list")


def arm_spawn(config, contract, server_config, scratch, source_env, *, harness_auth="account"):
    from ..arms import SpawnSurface
    from ..oss import require_enabled, load_catalog
    from ..schema import SchemaError

    settings = require_enabled(config.harness_id, config.model_id, source_env)
    if not config.model_id or not config.model_id.startswith("litellm/"):
        raise SchemaError("opencode requires an explicit litellm/<route> OSS model")
    route = config.model_id[len("litellm/"):]
    model = load_catalog()[route]
    if any(key.startswith(("OPENCODE_", "XDG_")) for key in config.env):
        raise SchemaError("opencode configuration environment is arm-controlled")
    root = scratch / "opencode"
    root.mkdir(mode=0o700, exist_ok=True)
    env = {}
    for key, directory in (("HOME", "home"), ("XDG_CONFIG_HOME", "config"),
                           ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state"),
                           ("XDG_CACHE_HOME", "cache"), ("XDG_RUNTIME_DIR", "run")):
        path = root / directory
        path.mkdir(mode=0o700, exist_ok=True)
        env[key] = str(path)
    mcp = {}
    permissions = {"*": "deny"}
    if contract.kind == "provider":
        if not server_config.get("command") or server_config.get("url"):
            raise SchemaError("opencode requires a local stdio MCP server")
        server_env = {}
        for index, (key, value) in enumerate(server_config.get("env", {}).items()):
            child_key = f"SEW_OPENCODE_MCP_{index}"
            env[child_key] = value
            server_env[key] = "{env:" + child_key + "}"
        mcp[contract.mcp_server_name] = {
            "type": "local", "command": [server_config["command"], *server_config.get("args", [])],
            "environment": server_env, "enabled": True,
        }
        permissions[f"{contract.mcp_server_name}_*"] = "allow"
    elif contract.kind == "native":
        permissions.update(webfetch="allow", websearch="allow")
    base_url = settings.base_url
    if contract.workspace_profile:
        # A GAP code cell gets the shell and file tools; the bench's portable
        # sandbox confines the whole tree. The endpoint is read from the env so
        # the Linux egress bridge can rewrite a loopback endpoint per launch.
        permissions.update({tool: "allow" for tool in WORKSPACE_TOOLS})
        base_url = "{env:SEW_LITELLM_BASE_URL}"
    document = {
        "$schema": "https://opencode.ai/config.json", "autoupdate": False,
        "share": "disabled", "snapshot": False, "plugin": [],
        "enabled_providers": [PROVIDER], "mcp": mcp,
        "permission": permissions,
        "tools": {"*": False, "bash": False, "shell": False, "webfetch": False, "websearch": False, **{key: True for key, value in permissions.items() if value == "allow"}},
        "provider": {PROVIDER: {"npm": "@ai-sdk/openai-compatible", "name": "Searchlight LiteLLM",
            "options": {"baseURL": base_url + "/v1", "apiKey": "{env:SEW_LITELLM_API_KEY}"},
            "models": {route: {"name": route, "limit": {
                "context": model["context_window_tokens"], "output": model["max_output_tokens"]}}}}},
    }
    path = root / "opencode.json"
    path.write_text(json.dumps(document, sort_keys=True) + "\n")
    path.chmod(0o600)
    env.update(OPENCODE_CONFIG=str(path), OPENCODE_CONFIG_DIR=str(root / "config"),
               OPENCODE_DISABLE_PROJECT_CONFIG="true", OPENCODE_DISABLE_MODELS_FETCH="true",
               OPENCODE_ENABLE_EXA="true" if contract.kind == "native" else "false")
    return SpawnSurface(contract, (), env, path)


SPEC = HarnessSpec(
    id="opencode", label="Opencode", live=True, bin_env="SEW_OPENCODE_BIN", default_bin="opencode",
    protocol="sew.harnesses.opencode_protocol:OpencodeProtocol", arm_spawn="sew.harnesses.opencode:arm_spawn",
    usage_parser="sew.harnesses.opencode_protocol:usage_row", pricing_key="oss-catalog", oss=True,
    native_search=True, minimum_version=MIN_VERSION, oss_model_only=True, code_cell_sandbox="portable",
    forbidden_flags=frozenset({"--model", "-m", "--format", "--agent", "--command", "--attach",
        "--session", "-s", "--continue", "-c", "--fork", "--dir", "--file", "-f", "--share",
        "--dangerously-skip-permissions", "--interactive", "-i", "--demo", "--pure", "--no-pure"}),
)
