"""Codex, a hosted harness (``codex exec --json``)."""

from __future__ import annotations

from .registry import HarnessSpec

# Arguments that could load another config, profile, model or tool set, or
# turn native search back on, over the arm's isolated CODEX_HOME.
FORBIDDEN_FLAGS = frozenset(
    {
        "-c",
        "--config",
        "--enable",
        "--search",
        "--ignore-user-config",
        "-m",
        "--model",
        "-p",
        "--profile",
    }
)

SPEC = HarnessSpec(
    id="codex",
    label="Codex",
    live=True,
    bin_env="SEW_CODEX_BIN",
    default_bin="codex",
    protocol="sew.live_harness:CodexProtocol",
    arm_spawn="sew.arms:codex_arm_spawn",
    forbidden_flags=FORBIDDEN_FLAGS,
    native_search=True,
    code_cell_sandbox="codex-sandbox",
    usage_parser="sew.live_harness:_codex_usage_row",
    pricing_key="price-table",
)
