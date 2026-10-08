"""Hermes Agent >= 0.16.0: isolated configuration and SQLite session events.

Verified against 0.16.0 --help and its oneshot/session-store implementation.
Oneshot stdout is plain final text; state.db is the authoritative tool ledger.
"""
from __future__ import annotations

import yaml

from .registry import HarnessSpec

MIN_VERSION = (0, 16, 0)


def arm_spawn(config, contract, server_config, scratch, source_env, *, harness_auth="account"):
    from sew.schema import SchemaError
    from sew.arms import SpawnSurface
    from sew.oss import require_enabled

    if config.harness_args:
        raise SchemaError("Hermes search cells do not accept caller harness arguments")
    settings = require_enabled("hermes", config.model_id, source_env)
    if not config.model_id or not config.model_id.startswith("litellm/"):
        raise SchemaError("Hermes requires an explicit litellm/<route> model")
    if contract.kind == "native" or contract.workspace_profile:
        raise SchemaError("Hermes supports provider and no-search search cells only")
    home = scratch / "hermes-home"
    home.mkdir(mode=0o700)
    path = home / "config.yaml"
    route = config.model_id.removeprefix("litellm/")
    toolsets = [f"mcp-{contract.mcp_server_name}"] if contract.kind == "provider" else []
    document = {
        "model": {"provider": "custom:searchlight", "default": route},
        "custom_providers": [{"name": "searchlight", "base_url": settings.base_url + "/v1",
                              "key_env": "SEW_LITELLM_API_KEY", "api_mode": "chat_completions"}],
        "mcp_servers": {contract.mcp_server_name: server_config} if toolsets else {},
        "platform_toolsets": {"cli": toolsets},
        "fallback_providers": [],
        # 0.16.0's platform resolver can recover non-configurable built-ins;
        # suppress them explicitly, including shell and every native web path.
        "agent": {"disabled_toolsets": (
            "web search x_search vision video image_gen video_gen computer_use "
            "terminal moa skills browser cronjob messaging file tts todo memory "
            "context_engine session_search clarify code_execution delegation "
            "homeassistant kanban discord discord_admin yuanbao feishu_doc "
            "feishu_drive spotify debugging"
        ).split()},
        "memory": {"memory_enabled": False, "user_profile_enabled": False},
    }
    path.write_text(yaml.safe_dump(document, sort_keys=True))
    path.chmod(0o600)
    args = ("--ignore-rules", "-t", contract.mcp_server_name) if toolsets else ("--ignore-rules",)
    return SpawnSurface(contract, args, {"HERMES_HOME": str(home), "HERMES_IGNORE_RULES": "1"}, path)


def usage_parser(row):
    return {"input": int(row.get("input_tokens") or 0),
            "output": int(row.get("output_tokens") or 0),
            "cached_input": int(row.get("cache_read_tokens") or 0),
            "reasoning": int(row.get("reasoning_tokens") or 0)}


SPEC = HarnessSpec(
    id="hermes", label="Hermes Agent", live=True, bin_env="SEW_HERMES_BIN", default_bin="hermes",
    protocol="sew.harnesses.hermes_protocol:HermesProtocol", arm_spawn="sew.harnesses.hermes:arm_spawn",
    forbidden_flags=frozenset({"-z", "--oneshot", "-m", "--model", "--provider", "-t", "--toolsets",
                               "--ignore-user-config", "--resume", "-r", "--continue", "-c", "--skills", "-s",
                               "--accept-hooks", "--yolo", "--worktree", "-w", "--tui", "--cli", "--dev"}),
    native_search=False, oss=True, usage_parser="sew.harnesses.hermes:usage_parser", pricing_key="oss-catalog", min_version=MIN_VERSION,
)
