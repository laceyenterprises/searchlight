"""Claude Code, a hosted harness (``claude --print``)."""

from __future__ import annotations

from .registry import HarnessSpec

# Arguments the arm contract sets itself: its MCP config, its tool sets and the
# model. A caller passing any of them could widen or swap the arm.
FORBIDDEN_FLAGS = frozenset(
    {
        "--mcp-config",
        "--strict-mcp-config",
        "--allowedTools",
        "--allowed-tools",
        "--disallowedTools",
        "--disallowed-tools",
        "--tools",
        "--model",
        "--fallback-model",
    }
)

SPEC = HarnessSpec(
    id="claude-code",
    label="Claude Code",
    live=True,
    bin_env="SEW_CLAUDE_CODE_BIN",
    default_bin="claude",
    protocol="sew.live_harness:ClaudeCodeProtocol",
    arm_spawn="sew.arms:claude_code_arm_spawn",
    forbidden_flags=FORBIDDEN_FLAGS,
    native_search=True,
    code_cell_sandbox="srt",
    usage_parser="sew.live_harness:_claude_usage_row",
    pricing_key="price-table",
)
