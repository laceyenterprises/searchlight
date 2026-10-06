"""Pricing parity of configured/discovered tools and coverage of stored calls."""

from collections import Counter
from collections.abc import Mapping
import json
import yaml

from .arms import PROVIDER_SERVER_NAMES
from .catalog import module_root
from .cost_model import vendor_call_from_record, vendor_call_cost
from .mcp_meter import _record_meter_observed, load_tariffs


def require_pricing_parity(enabled_tools, tariffs=None, measured_sources=None):
    """Reject enabled tools without a rule or a documented response receipt.

    An explicit unpriced rule documents a known gap; it does not claim a price.
    Call with tools/list names or the arm configuration's enabled_tools list.
    """
    tariffs = load_tariffs() if tariffs is None else tariffs
    measured_sources = measured_sources or {}
    unavailable = [
        provider
        for provider, tools in enabled_tools.items()
        if tools and not tariffs.get(provider) and not measured_sources.get(provider)
    ]
    if unavailable:
        raise ValueError(
            "pricing tariff registry unavailable for: " + ", ".join(sorted(unavailable))
        )
    missing = [
        f"{provider}:{tool}"
        for provider, tools in enabled_tools.items()
        for tool in tools
        if not tariffs.get(provider, {}).get(tool)
        and not measured_sources.get(provider, {}).get(tool)
    ]
    if missing:
        raise ValueError("pricing parity missing: " + ", ".join(sorted(missing)))


def configured_pricing_tools(provider, config):
    """Resolve arm tool selection, falling back to the captured default surface.

    tools/list also records uncovered tools at runtime to detect upstream drift.
    The snapshot carries MCP version pins and contains no authentication data.
    """
    from urllib.parse import parse_qs, urlsplit

    manifest = yaml.safe_load((module_root() / "config/provider-arm-tools.yaml").read_text())
    tools = config.get("enabled_tools")
    if tools is None and provider == "exa":
        env = config.get("env") or {}
        selected = env.get("ENABLED_TOOLS", env.get("TOOLS"))
        for arg in [config.get("url"), *(config.get("args") or [])]:
            if not isinstance(arg, str):
                continue
            try:
                parsed = urlsplit(arg)
                hostname = parsed.hostname
            except ValueError:
                continue
            if hostname == "mcp.exa.ai":
                selected = parse_qs(parsed.query).get("tools", [selected])[-1]
        if selected:
            tools = [t.strip() for t in selected.split(",") if t.strip()]
    if tools is None:
        tools = manifest["providers"][provider]["enabled_tools"]
    if not isinstance(tools, list) or not all(isinstance(t, str) and t for t in tools):
        raise ValueError(f"{provider}: enabled_tools must be a list of tool names")
    disabled = config.get("disabled_tools", [])
    if not isinstance(disabled, list) or not all(isinstance(t, str) and t for t in disabled):
        raise ValueError(f"{provider}: disabled_tools must be a list of tool names")
    return [t for t in tools if t not in disabled]


def pricing_coverage(rows, table):
    """Count priced call records per vendor, independently of model-token usage.

    Missing metering and missing receipts are visible; zero records never
    establishes full coverage. Only references inside a bundle are read.
    """
    result = {}
    for row in rows:
        provider = row.provider_id
        if provider not in PROVIDER_SERVER_NAMES:
            continue
        own = result.setdefault(
            provider,
            {
                "calls_n": 0,
                "priced_n": 0,
                "estimated_n": 0,
                "unpriced_n": 0,
                "cells_n": 0,
                "unobserved_cells_n": 0,
                "missing_tool_rules_n": 0,
                "unpriced_reasons": Counter(),
            },
        )
        own["cells_n"] += 1
        root = row.run_dir
        discovery = {}
        try:
            discovery = (
                json.loads((root / "provider-calls/availability.json").read_text()) if root else {}
            )
        except (OSError, ValueError, AttributeError):
            pass
        if _record_meter_observed(discovery, provider, row.run_id):
            missing = discovery.get("pricing_missing_tools_n", 0)
            if isinstance(missing, int) and not isinstance(missing, bool) and missing >= 0:
                own["missing_tool_rules_n"] += missing
        else:
            own["unobserved_cells_n"] += 1
        try:
            run = json.loads((root / "run.json").read_text()) if root else {}
        except (OSError, ValueError):
            run = {}
        refs = run.get("provider_call_refs", []) if isinstance(run, Mapping) else []
        for ref in refs if isinstance(refs, list) else []:
            own["calls_n"] += 1
            try:
                path = (root / ref).resolve()
                path.relative_to(root.resolve())
                record = json.loads(path.read_text())
                response = record.get("response", {})
                component = vendor_call_cost(
                    vendor_call_from_record(record, provider_id=provider), table
                )
                reason = response.get("meter", {}).get("unpriced_reason") or component["reason"]
            except (OSError, ValueError, TypeError, AttributeError):
                component = {"amount_usd": None, "basis": "unknown"}
                reason = "provider_call_record_unreadable"
            if component["amount_usd"] is None:
                own["unpriced_n"] += 1
                own["unpriced_reasons"][reason or "pricing_unknown"] += 1
            else:
                own["priced_n"] += 1
                own["estimated_n"] += component["basis"] == "estimated"
    for own in result.values():
        own["coverage"] = own["priced_n"] / own["calls_n"] if own["calls_n"] else None
        own["lower_bound"] = bool(
            own["unpriced_n"] or own["unobserved_cells_n"] or own["missing_tool_rules_n"]
        )
        own["unpriced_reasons"] = dict(sorted(own["unpriced_reasons"].items()))
    return dict(sorted(result.items()))


def render_pricing_coverage(coverage):
    lines = [
        "## Vendor pricing coverage",
        "",
        "Cost columns are lower bounds wherever calls are unpriced or metering is missing. "
        "Request-based page counts are estimates; they may overstate successful pages.",
        "",
        "| vendor | priced / recorded calls | estimated calls | unobserved cells | missing tool rules | unpriced reasons |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for vendor, own in coverage.items():
        reasons = ", ".join(f"{k}: {v}" for k, v in own["unpriced_reasons"].items()) or "none"
        lines.append(
            f"| {vendor} | {own['priced_n']}/{own['calls_n']} | "
            f"{own['estimated_n']} | {own['unobserved_cells_n']} | {own['missing_tool_rules_n']} | {reasons} |"
        )
    return "\n".join(lines)
