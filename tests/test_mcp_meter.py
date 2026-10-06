"""sew.mcp_meter: metering a live arm's vendor MCP server without changing it."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from conftest import MODULE_ROOT
from test_bakeoff_bundle import _assemble, _mixed_run, _run_id
from test_bakeoff_report import FACT, GENERATED_AT, _arm, _run_root, _state, _usage

from sew.bakeoff_report import build_bakeoff_report
from sew.cost_model import load_price_table, vendor_call, vendor_call_cost
from sew.harness import HarnessRunConfig, ProviderExposure
from sew import mcp_meter
from sew.live_harness import LIVE_ENV, run_live_harness
from sew.mcp_meter import (
    CallMeter,
    default_tariffs_path,
    load_tariffs,
    metered_server_config,
)
from sew.schema import validate_fixture_run, validate_provider_call

TARIFFS = load_tariffs()
CLOCK = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)

# A vendor MCP server that answers initialize and tools/call, and never answers
# a call whose query is "hang" (the meter must still record it).
FAKE_VENDOR = r"""
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    if message.get("method") == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake-vendor", "version": "1"}}
    elif message.get("method") == "tools/list":
        result = {"tools": [{"name": "brave_web_search", "inputSchema": {"type": "object"}}]}
    elif message.get("method") == "tools/call":
        if message["params"]["arguments"].get("query") == "hang":
            continue
        body = {"results": [{"url": "https://example.test/a"}, {"url": "https://example.test/b"}]}
        result = {"content": [{"type": "text", "text": json.dumps(body)}]}
    else:
        result = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}) + "\n")
    sys.stdout.flush()
"""

# A claude-code stand-in that really launches the MCP server its --mcp-config
# names, makes one tools/call through it, then reports that call in its
# stream-json transcript.
FAKE_HARNESS = r"""
import json, os, subprocess, sys
argv = sys.argv[1:]
config = json.load(open(argv[argv.index("--mcp-config") + 1]))
(server_name, server), = config["mcpServers"].items()
sys.stdin.read()
env = {**os.environ, **server.get("env", {})}
proc = subprocess.Popen([server["command"], *server.get("args", [])], env=env,
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
def rpc(message):
    proc.stdin.write(json.dumps(message) + "\n"); proc.stdin.flush()
    return json.loads(proc.stdout.readline())
rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
rpc({"jsonrpc": "2.0", "id": "list", "method": "tools/list", "params": {}})
reply = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "brave_web_search", "arguments": {"query": "release"}}})
proc.stdin.close(); proc.wait()
tool = f"mcp__{server_name}__brave_web_search"
usage = {"input_tokens": 12, "output_tokens": 40}
def emit(event):
    sys.stdout.write(json.dumps(event) + "\n"); sys.stdout.flush()
emit({"type": "system", "subtype": "init", "tools": [tool], "model": "fake"})
emit({"type": "assistant", "message": {"id": "m1", "role": "assistant", "content": [
      {"type": "tool_use", "id": "t1", "name": tool, "input": {"query": "release"}}],
      "usage": usage}})
emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
      "content": reply["result"]["content"][0]["text"]}]}})
text = json.dumps({"answer": "Release 3.2", "citation_urls": ["https://example.test/a"]})
emit({"type": "assistant", "message": {"id": "m2", "role": "assistant",
      "content": [{"type": "text", "text": text}], "usage": usage}})
emit({"type": "result", "subtype": "success", "is_error": False, "result": text,
      "usage": usage, "num_turns": 2})
"""


def _meter(tmp_path: Path, provider: str) -> CallMeter:
    return CallMeter(
        provider_id=provider,
        run_id="run-1",
        call_dir=tmp_path / "provider-calls",
        tariffs=TARIFFS,
        clock=lambda: CLOCK,
    )


def _call(
    meter: CallMeter, tool: str, arguments: dict[str, Any], result: Any, *, error: Any = None
) -> dict[str, Any]:
    meter.client_message(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
    )
    reply: dict[str, Any] = {"jsonrpc": "2.0", "id": 7}
    reply["error" if error is not None else "result"] = error if error is not None else result
    meter.server_message(reply)
    (path,) = list((meter.call_dir).glob("*-*.json"))
    record = json.loads(path.read_text())
    path.unlink()
    return validate_provider_call(record, expected_run_id="run-1")


def _text(body: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(body)}]}


# ── Pricing evidence ─────────────────────────────────────────────────


def test_every_tariff_names_a_row_the_price_table_can_price() -> None:
    table = load_price_table()
    tariffs = load_tariffs(default_tariffs_path())
    assert {"brave", "perplexity", "tavily", "firecrawl"} <= set(tariffs)
    for provider, tools in tariffs.items():
        for tool, rule in tools.items():
            vendor = table.vendors.get(provider)
            assert vendor is not None, (provider, tool)
            if rule.get("unpriced_reason"):
                assert isinstance(rule["unpriced_reason"], str)
                continue
            if "pricing_tier" in rule:
                tier = rule["pricing_tier"]
                tiers = tier["values"].values() if isinstance(tier, dict) else [tier]
                for name in tiers:
                    assert name in vendor["usd_per_request"], (provider, tool, name)
            else:
                assert vendor["meter"] == "credits", (provider, tool)


def test_a_brave_search_is_priced_at_its_published_request_rate(tmp_path: Path) -> None:
    record = _call(
        _meter(tmp_path, "brave"), "brave_web_search", {"query": "x"}, _text({"web": {}})
    )

    assert record["status"] == "ok"
    assert record["response"]["pricing_tier"] == "search:request"
    assert record["response"]["meter"] == {
        "tool": "brave_web_search",
        "via": "sew.mcp_meter",
        "basis": "tariff",
    }
    cost = vendor_call_cost(
        vendor_call(
            provider_id="brave",
            status=record["status"],
            ended_at=record["ended_at"],
            pricing_tier=record["response"]["pricing_tier"],
        ),
        load_price_table(),
    )
    assert (cost["amount_usd"], cost["basis"]) == (pytest.approx(0.005), "inferred")


@pytest.mark.parametrize(("depth", "credits"), [(None, 1), ("basic", 1), ("advanced", 2)])
def test_tavily_search_credits_follow_search_depth(
    tmp_path: Path, depth: str | None, credits: int
) -> None:
    arguments = {"query": "x", **({"search_depth": depth} if depth else {})}
    record = _call(_meter(tmp_path, "tavily"), "tavily_search", arguments, _text({"results": []}))

    assert record["response"]["provider_units"] == {
        "credits_used": credits,
        "credits_source": "tariff",
    }


@pytest.mark.parametrize("depth", [None, "basic"])
def test_tavily_server_depth_override_is_unpriced_without_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, depth: str | None
) -> None:
    # Tavily MCP applies DEFAULT_PARAMETERS after the observed tools/call,
    # overriding even an explicit basic depth before its API request.
    monkeypatch.setenv("DEFAULT_PARAMETERS", '{"search_depth":"advanced"}')
    arguments = {"query": "x", **({"search_depth": depth} if depth else {})}
    record = _call(_meter(tmp_path, "tavily"), "tavily_search", arguments, _text({"results": []}))
    assert (
        record["response"]["meter"]["unpriced_reason"]
        == "server_default_changes_price:search_depth"
    )
    assert "provider_units" not in record["response"]

    measured = _call(
        _meter(tmp_path, "tavily"),
        "tavily_search",
        arguments,
        _text({"results": [], "usage": {"credits": 2}}),
    )
    assert measured["response"]["provider_units"] == {
        "credits_used": 2,
        "credits_source": "response",
    }


@pytest.mark.parametrize(
    ("depth", "urls", "credits"), [("basic", 7, 2), ("advanced", 7, 4), ("basic", 5, 1)]
)
def test_tavily_extract_credits_are_per_started_block_of_five_urls(
    tmp_path: Path, depth: str, urls: int, credits: int
) -> None:
    body = {"results": [{"url": f"https://e.test/{i}"} for i in range(urls)]}
    record = _call(
        _meter(tmp_path, "tavily"), "tavily-extract", {"extract_depth": depth}, _text(body)
    )

    assert record["response"]["provider_units"]["credits_used"] == credits


def test_tavily_extract_text_only_estimates_from_requested_urls_without_leaking_them(
    tmp_path: Path,
) -> None:
    urls = [f"https://secret.test/{i}" for i in range(6)]
    record = _call(
        _meter(tmp_path, "tavily"),
        "tavily_extract",
        {"query": "private", "urls": urls},
        {"content": [{"type": "text", "text": "Plain extracted text"}]},
    )
    assert record["response"]["meter"]["basis"] == "estimated_from_request"
    assert record["response"]["provider_units"] == {
        "credits_used": 2,
        "credits_source": "estimated_from_request",
        "requested_url_count": 6,
    }
    assert record["request"]["unrecognized_argument_key_count"] == 0
    assert not any(url in json.dumps(record) for url in urls)
    priced = vendor_call_cost(
        vendor_call(
            provider_id="tavily",
            status="ok",
            ended_at=CLOCK.isoformat(),
            provider_units=record["response"]["provider_units"],
        ),
        load_price_table(),
    )
    assert priced["basis"] == "estimated"
    assert priced["amount_usd"] == pytest.approx(0.016)


def test_firecrawl_search_rounds_up_per_ten_results_and_scrape_formats_add_credits(
    tmp_path: Path,
) -> None:
    meter = _meter(tmp_path, "firecrawl")
    eleven = _text({"data": [{"url": f"https://e.test/{i}"} for i in range(11)]})
    assert (
        _call(meter, "firecrawl_search", {"query": "x"}, eleven)["response"]["provider_units"][
            "credits_used"
        ]
        == 4
    )
    scraped = _call(
        meter,
        "firecrawl_search",
        {"query": "x", "scrapeOptions": {"formats": ["markdown"]}},
        eleven,
    )
    assert scraped["response"]["meter"]["unpriced_reason"] == "argument_changes_price:scrapeOptions"
    assert "provider_units" not in scraped["response"]
    json_scrape = _call(
        meter, "firecrawl_scrape", {"url": "u", "formats": [{"type": "json"}]}, _text({})
    )
    assert json_scrape["response"]["provider_units"]["credits_used"] == 5
    plain = _call(meter, "firecrawl_scrape", {"url": "u", "formats": ["markdown"]}, _text({}))
    assert plain["response"]["provider_units"]["credits_used"] == 1


def test_measured_spend_in_the_response_wins_over_any_tariff(tmp_path: Path) -> None:
    exa = _call(
        _meter(tmp_path, "exa"),
        "web_search_exa",
        {"query": "x"},
        _text({"costDollars": {"total": 0.007}}),
    )
    assert exa["response"]["provider_cost"] == {"currency": "USD", "total": 0.007}
    assert exa["response"]["meter"]["basis"] == "response"

    tavily = _call(
        _meter(tmp_path, "tavily"),
        "tavily_search",
        {"search_depth": "advanced"},
        _text({"results": [], "usage": {"credits": 3}}),
    )
    assert tavily["response"]["provider_units"] == {"credits_used": 3, "credits_source": "response"}

    ask = _call(
        _meter(tmp_path, "perplexity"),
        "perplexity_ask",
        {"messages": []},
        {"structuredContent": {"usage": {"cost": {"total_cost": 0.0123}}}, "content": []},
    )
    assert ask["response"]["provider_cost"]["total"] == 0.0123


def test_an_unknown_tool_or_a_failed_call_is_never_priced(tmp_path: Path) -> None:
    unknown = _call(_meter(tmp_path, "brave"), "unregistered_vendor_tool", {"key": "k"}, _text({}))
    assert unknown["response"]["meter"] == {
        "tool": "unknown",
        "via": "sew.mcp_meter",
        "basis": "unpriced",
        "unpriced_reason": "no_tariff_for_tool",
    }
    assert unknown["operation"] == unknown["request"]["tool"] == "unknown"
    assert "pricing_tier" not in unknown["response"]

    failed = _call(
        _meter(tmp_path, "brave"),
        "brave_web_search",
        {"query": "x"},
        {"isError": True, "content": [{"type": "text", "text": "upstream exploded"}]},
    )
    assert (failed["status"], failed["error_class"]) == ("failed", "tool_error")
    assert failed["response"]["error_kind"] == "other"
    assert failed["response"]["meter"]["unpriced_reason"] == "call_failed"
    assert "upstream exploded" not in json.dumps(failed)

    limited = _call(
        _meter(tmp_path, "brave"),
        "brave_web_search",
        {"query": "x"},
        None,
        error={"code": -32000, "message": "HTTP 429 Too Many Requests"},
    )
    assert limited["status"] == "rate_limited"
    assert limited["response"]["error_kind"] == "http_error"
    assert limited["response"]["meter"]["unpriced_reason"] == "http_request_failed"
    assert "429 Too Many Requests" not in json.dumps(limited)


def test_brave_empty_web_result_is_billed_without_storing_content(tmp_path: Path) -> None:
    record = _call(
        _meter(tmp_path, "brave"),
        "brave_web_search",
        {"query": "x"},
        {"isError": True, "content": [{"type": "text", "text": "No web results found"}]},
    )
    assert record["status"] == "failed"
    assert record["response"]["error_kind"] == "empty_result"
    assert record["response"]["meter"]["basis"] == "billed_empty_result"
    assert record["response"]["pricing_tier"] == "search:request"
    assert "No web results found" not in json.dumps(record)

    # The summarizer's identical error text can follow a caught HTTP failure.
    summarizer = _call(
        _meter(tmp_path, "brave"),
        "brave_summarizer",
        {"key": "k"},
        {
            "isError": True,
            "content": [{"type": "text", "text": "Unable to retrieve a Summarizer summary."}],
        },
    )
    assert summarizer["response"]["meter"]["basis"] == "unpriced"


def test_codex_tavily_crawl_name_is_preserved_but_variable_credits_are_unpriced(
    tmp_path: Path,
) -> None:
    crawl = _call(
        _meter(tmp_path, "tavily"),
        "tavily_crawl",
        {"url": "https://example.test", "limit": 1, "max_depth": 1},
        _text({"results": []}),
    )
    assert crawl["request"]["tool"] == crawl["operation"] == "tavily_crawl"
    assert crawl["response"]["meter"]["unpriced_reason"] == "crawl_credits_not_reported"

    alias = _call(
        _meter(tmp_path, "tavily"),
        "tavily-crawl",
        {"url": "https://example.test"},
        _text({"results": []}),
    )
    assert alias["operation"] == "tavily-crawl"
    assert alias["response"]["meter"]["unpriced_reason"] == "crawl_credits_not_reported"


def test_malformed_tool_name_is_not_persisted_by_direct_record_call(tmp_path: Path) -> None:
    meter = _meter(tmp_path, "tavily")
    record = meter.record(
        {
            "tool": {"name": "Acme board plan"},
            "arguments": {},
            "started_at": "2026-09-27T12:00:00Z",
        },
        None,
    )
    assert record["operation"] == record["request"]["tool"] == "unknown"
    assert record["response"]["meter"]["tool"] == "unknown"
    assert "Acme board plan" not in json.dumps(record)


def test_free_text_arguments_never_enter_a_record(tmp_path: Path) -> None:
    # A publication bundle copies provider-call records without transcript
    # redaction, so a record must never hold a query, URL or prompt, even one
    # with no credential-shaped text.
    secret = "sk-ant-api03-" + "Q7" * 16
    confidential = "Acme Corp acquisition of Globex, board-only Q3 plan"
    record = _call(
        _meter(tmp_path, "tavily"),
        "tavily_search",
        {"query": f"{confidential} {secret}", "search_depth": "advanced"},
        _text({"results": []}),
    )
    text = json.dumps(record)
    assert confidential not in text and secret not in text
    assert record["request"]["argument_keys"] == ["query", "search_depth"]
    assert record["request"]["unrecognized_argument_key_count"] == 0
    assert record["request"]["pricing_arguments"] == {"search_depth": "advanced"}
    assert "arguments_sha256" not in record["request"]
    assert "content_sha256" not in record["response"]


def test_firecrawl_credits_cost_receipt_is_measured(tmp_path: Path) -> None:
    meter = _meter(tmp_path, "firecrawl")
    receipt = _call(
        meter,
        "firecrawl_scrape",
        {"url": "u", "alexandria": {"capability": "x"}},
        _text({"success": True, "data": {"alexandria": [], "creditsCost": 10}}),
    )
    assert receipt["response"]["provider_units"] == {
        "credits_used": 10,
        "credits_source": "response",
    }
    assert receipt["response"]["meter"]["basis"] == "response"
    assert receipt["request"]["pricing_arguments"] == {"alexandria": "<present>"}

    retained = _call(
        meter,
        "firecrawl_scrape",
        {"alexandria": [{"provider": "fred", "capability": "series/observations"}]},
        _text({"creditsCost": 3, "data": {"alexandria": []}}),
    )
    assert retained["response"]["provider_units"] == {
        "credits_used": 3,
        "credits_source": "response",
    }

    no_receipt = _call(
        meter, "firecrawl_scrape", {"url": "u", "alexandria": {"capability": "x"}}, _text({})
    )
    assert no_receipt["response"]["meter"]["unpriced_reason"] == "argument_changes_price:alexandria"
    assert "provider_units" not in no_receipt["response"]

    invalid_receipt = _call(
        meter,
        "firecrawl_scrape",
        {"alexandria": [{"provider": "fred", "capability": "series/observations"}]},
        _text({"success": True, "data": {"creditsCost": "10"}}),
    )
    assert (
        invalid_receipt["response"]["meter"]["unpriced_reason"]
        == "argument_changes_price:alexandria"
    )


@pytest.mark.parametrize(
    ("arguments", "tier", "reason"),
    [
        ({"query": "q"}, "search:web", None),
        ({"query": "q", "search_type": "web"}, "search:web", None),
        ({"query": "q", "search_type": "fast"}, "search:fast", None),
        ({"query": "q", "search_type": "turbo"}, None, "unrecognized_argument:search_type"),
    ],
)
def test_perplexity_search_tier_follows_search_type(
    tmp_path: Path, arguments: dict[str, Any], tier: str | None, reason: str | None
) -> None:
    record = _call(
        _meter(tmp_path, "perplexity"), "perplexity_search", arguments, _text({"results": []})
    )
    assert record["response"].get("pricing_tier") == tier
    assert record["response"]["meter"].get("unpriced_reason") == reason


def test_an_unrecognized_priced_argument_value_is_unpriced_not_defaulted(tmp_path: Path) -> None:
    record = _call(
        _meter(tmp_path, "tavily"),
        "tavily_search",
        {"query": "q", "search_depth": "ultra"},
        _text({"results": []}),
    )
    assert record["response"]["meter"]["unpriced_reason"] == "unrecognized_argument:search_depth"
    assert "provider_units" not in record["response"]
    assert record["request"]["pricing_arguments"] == {"search_depth": "<unrecognized>"}


def test_publication_omits_unrecognized_values_and_keys_even_when_call_fails(
    tmp_path: Path,
) -> None:
    root, prices = _mixed_run(tmp_path)
    run_id = _run_id("exa", 1)
    meter = CallMeter(
        provider_id="tavily",
        run_id=run_id,
        call_dir=root / "bundles" / run_id / "provider-calls",
        tariffs=TARIFFS,
        clock=lambda: CLOCK,
    )
    confidential_value = "Acme board Q3 acquisition selector"
    confidential_key = "Acme board Q3 acquisition key"
    for call_id, arguments, reply in (
        (
            1,
            {"query": "public", "search_depth": confidential_value},
            {"result": _text({"results": []})},
        ),
        (
            2,
            {"query": "public", confidential_key: "unused"},
            {"error": {"code": -32602, "message": "invalid arguments"}},
        ),
    ):
        meter.client_message(
            {
                "id": call_id,
                "method": "tools/call",
                "params": {
                    "name": "tavily_search",
                    "arguments": arguments,
                },
            }
        )
        meter.server_message({"id": call_id, **reply})

    artifacts = _assemble(root, tmp_path / "pub", prices)
    published_dir = (
        artifacts.bundle_dir / "runs" / root.name / "bundles" / run_id / "provider-calls"
    )
    published = [json.loads(path.read_text()) for path in published_dir.glob("tavily-*.json")]
    assert len(published) == 2
    assert all(confidential_value not in json.dumps(call) for call in published)
    assert all(confidential_key not in json.dumps(call) for call in published)
    successful = next(call for call in published if call["status"] == "ok")
    rejected = next(call for call in published if call["status"] == "failed")
    assert (
        successful["response"]["meter"]["unpriced_reason"] == "unrecognized_argument:search_depth"
    )
    assert successful["request"]["pricing_arguments"] == {"search_depth": "<unrecognized>"}
    assert rejected["request"]["argument_keys"] == ["query"]
    assert rejected["request"]["unrecognized_argument_key_count"] == 1


def test_failed_call_with_confidential_tool_name_cannot_publish_that_name(tmp_path: Path) -> None:
    root, prices = _mixed_run(tmp_path)
    run_id = _run_id("exa", 1)
    meter = CallMeter(
        provider_id="tavily",
        run_id=run_id,
        call_dir=root / "bundles" / run_id / "provider-calls",
        tariffs=TARIFFS,
        clock=lambda: CLOCK,
    )
    confidential_name = "tavily_search_Acme_board_plan"
    meter.client_message(
        {"id": 9, "method": "tools/call", "params": {"name": confidential_name, "arguments": {}}}
    )
    meter.server_message({"id": 9, "error": {"code": -32601, "message": "unknown tool"}})

    artifacts = _assemble(root, tmp_path / "pub", prices)
    published_dir = (
        artifacts.bundle_dir / "runs" / root.name / "bundles" / run_id / "provider-calls"
    )
    published = [json.loads(path.read_text()) for path in published_dir.glob("tavily-*.json")]
    assert len(published) == 1
    call = published[0]
    assert call["status"] == "failed"
    assert (
        call["operation"]
        == call["request"]["tool"]
        == call["response"]["meter"]["tool"]
        == "unknown"
    )
    assert confidential_name not in json.dumps(call)
    identity = "\0".join((run_id, "tavily", "unknown", call["started_at"], "1"))
    expected_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
    assert call["call_id"] == f"tavily-{expected_id}"


def test_published_bundle_cannot_match_a_guessed_confidential_query_hash(tmp_path: Path) -> None:
    root, prices = _mixed_run(tmp_path)
    run_id = _run_id("exa", 1)
    confidential_query = "merger"
    arguments = {"query": confidential_query}
    result = _text({"results": [confidential_query]})
    meter = CallMeter(
        provider_id="tavily",
        run_id=run_id,
        call_dir=root / "bundles" / run_id / "provider-calls",
        tariffs=TARIFFS,
        clock=lambda: CLOCK,
    )
    meter.client_message(
        {
            "id": 8,
            "method": "tools/call",
            "params": {"name": "tavily_search", "arguments": arguments},
        }
    )
    meter.server_message({"id": 8, "result": result})

    artifacts = _assemble(root, tmp_path / "pub", prices)
    published_dir = (
        artifacts.bundle_dir / "runs" / root.name / "bundles" / run_id / "provider-calls"
    )
    (published_path,) = published_dir.glob("tavily-*.json")
    published = published_path.read_text()
    candidate_argument_hash = hashlib.sha256(
        json.dumps(arguments, sort_keys=True).encode()
    ).hexdigest()
    candidate_response_hash = hashlib.sha256(result["content"][0]["text"].encode()).hexdigest()
    assert confidential_query not in published
    assert candidate_argument_hash not in published
    assert candidate_response_hash not in published
    assert "arguments_sha256" not in published
    assert "content_sha256" not in published


def test_scrape_formats_record_only_the_priced_format_names(tmp_path: Path) -> None:
    record = _call(
        _meter(tmp_path, "firecrawl"),
        "firecrawl_scrape",
        {
            "url": "https://intranet.example/private",
            "formats": ["markdown", {"type": "json", "prompt": "extract the salary table"}],
        },
        _text({}),
    )
    assert record["request"]["pricing_arguments"] == {"formats": ["json"]}
    assert "salary" not in json.dumps(record) and "intranet" not in json.dumps(record)
    assert record["response"]["provider_units"]["credits_used"] == 5


# ── The stdio relay ──────────────────────────────────────────────────


def _proxy(
    tmp_path: Path, provider: str = "brave", server_env: dict[str, str] | None = None
) -> tuple[subprocess.Popen[str], Path]:
    vendor = tmp_path / "vendor.py"
    vendor.write_text(FAKE_VENDOR, encoding="utf-8")
    call_dir = tmp_path / "provider-calls"
    config = metered_server_config(
        {"command": sys.executable, "args": [str(vendor)], "env": server_env or {}},
        provider_id=provider,
        run_id="run-1",
        call_dir=call_dir,
    )
    assert config is not None
    proc = subprocess.Popen(
        [config["command"], *config["args"]],
        env={**os.environ, **config["env"]},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    return proc, call_dir


def test_the_relay_is_transparent_and_records_each_tool_call(tmp_path: Path) -> None:
    proc, call_dir = _proxy(tmp_path)
    assert proc.stdin and proc.stdout
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "brave_web_search", "arguments": {"query": "q"}},
        },
    ]
    for message in messages:
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()
    first = json.loads(proc.stdout.readline())
    second = json.loads(proc.stdout.readline())
    proc.stdin.close()
    assert proc.wait(timeout=20) == 0

    assert first["result"]["serverInfo"]["name"] == "fake-vendor"
    assert (
        json.loads(second["result"]["content"][0]["text"])["results"][0]["url"]
        == "https://example.test/a"
    )
    (record,) = [json.loads(p.read_text()) for p in call_dir.glob("*-*.json")]
    validate_provider_call(record, expected_run_id="run-1")
    assert (record["operation"], record["status"], record["response"]["pricing_tier"]) == (
        "brave_web_search",
        "ok",
        "search:request",
    )


def test_tavily_server_config_defaults_reach_the_meter(tmp_path: Path) -> None:
    proc, call_dir = _proxy(
        tmp_path, "tavily", {"DEFAULT_PARAMETERS": '{"search_depth":"advanced"}'}
    )
    assert proc.stdin and proc.stdout
    proc.stdin.write(
        json.dumps(
            {
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "tavily_search",
                    "arguments": {"query": "x", "search_depth": "basic"},
                },
            }
        )
        + "\n"
    )
    proc.stdin.close()
    assert proc.wait(timeout=20) == 0
    (record,) = [json.loads(path.read_text()) for path in call_dir.glob("*-*.json")]
    assert (
        record["response"]["meter"]["unpriced_reason"]
        == "server_default_changes_price:search_depth"
    )
    assert "provider_units" not in record["response"]


def test_a_call_the_server_never_answers_is_recorded_as_failed(tmp_path: Path) -> None:
    proc, call_dir = _proxy(tmp_path)
    assert proc.stdin
    proc.stdin.write(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": "brave_web_search", "arguments": {"query": "hang"}},
            }
        )
        + "\n"
    )
    proc.stdin.close()
    assert proc.wait(timeout=20) == 0

    (record,) = [json.loads(p.read_text()) for p in call_dir.glob("*-*.json")]
    assert (record["status"], record["error_class"]) == ("failed", "no_response")
    assert "pricing_tier" not in record["response"]


def test_a_remote_server_is_left_unwrapped() -> None:
    assert (
        metered_server_config(
            {"type": "http", "url": "https://mcp.test"},
            provider_id="parallel-web",
            run_id="r",
            call_dir=Path("/tmp/x"),
        )
        is None
    )


def test_frames_that_are_not_json_are_counted_and_skipped(tmp_path: Path) -> None:
    meter = _meter(tmp_path, "brave")
    meter.client_line(b"Content-Length: 12\n")
    meter.server_line(b"\xff\xfe{\n")
    assert meter.bad_frames == 2
    meter.client_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "brave_web_search", "arguments": {"query": "q"}},
            }
        ).encode()
    )
    meter.server_line(json.dumps({"jsonrpc": "2.0", "id": 3, "result": _text({})}).encode())
    (path,) = list(meter.call_dir.glob("*-*.json"))
    assert json.loads(path.read_text())["status"] == "ok"


def test_the_shared_measured_spend_keeps_the_keywords_sew_calls_it_with() -> None:
    from sew import meter_pricing as pricing

    parameters = inspect.signature(pricing.measured_spend).parameters
    for name in ("server", "arguments"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[name].default is None
    # SEW's record() depends on `server` steering which receipts are trusted.
    receipt = [{"creditsCost": 3}]
    billed = {"alexandria": "on"}
    assert pricing.measured_spend(receipt, server="firecrawl", arguments=billed) == {
        "provider_units": {"credits_used": 3, "credits_source": "response"}
    }
    assert pricing.measured_spend(receipt, server="exa", arguments=billed) == {}


def test_a_meter_without_the_shared_core_records_calls_unpriced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp_meter, "_core_cache", [None])
    assert mcp_meter.load_tariffs() == {}
    assert (
        metered_server_config(
            {"command": "vendor"}, provider_id="brave", run_id="r", call_dir=tmp_path
        )
        is None
    )
    record = _call(_meter(tmp_path, "brave"), "brave_web_search", {"query": "q"}, _text({}))
    assert record["response"]["meter"] == {
        "tool": "brave_web_search",
        "via": "sew.mcp_meter",
        "basis": "unpriced",
        "unpriced_reason": mcp_meter.CORE_UNAVAILABLE,
    }


def test_a_checkout_without_the_shared_core_still_imports_and_relays(tmp_path: Path) -> None:
    # Outside the monorepo SEW keeps its portable local meter.
    lib = tmp_path / "lib"
    shutil.copytree(MODULE_ROOT / "lib" / "python" / "sew", lib / "sew")
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["PYTHONPATH"] = str(lib)
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import sew.mcp_meter as m; "
            "print(m.metered_server_config({'command': 'x'}, provider_id='p', run_id='r', "
            "call_dir=Path('.')))",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "sew.mcp_meter" in probe.stdout
    assert mcp_meter.CORE_UNAVAILABLE not in probe.stderr
    call_dir = tmp_path / "provider-calls"
    relay = subprocess.run(
        [
            sys.executable,
            "-m",
            "sew.mcp_meter",
            "--provider",
            "brave",
            "--run-id",
            "r",
            "--call-dir",
            str(call_dir),
            "--",
            sys.executable,
            "-c",
            "import sys; sys.stdout.write(sys.stdin.read())",
        ],
        cwd=tmp_path,
        env=env,
        input='{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n',
        capture_output=True,
        text=True,
        check=True,
    )
    assert relay.stdout == '{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n'
    assert json.loads((call_dir / "availability.json").read_text())["observation_reason"] is None


# ── End to end: live harness → metered bundle → report ──────────────


def test_a_live_provider_run_writes_priced_provider_call_records(tmp_path: Path) -> None:
    harness = tmp_path / "fake-claude"
    harness.write_text(f"#!{sys.executable}\n{FAKE_HARNESS}", encoding="utf-8")
    harness.chmod(0o755)
    vendor = tmp_path / "vendor.py"
    vendor.write_text(FAKE_VENDOR, encoding="utf-8")
    config = HarnessRunConfig(
        harness_id="claude-code",
        provider_id="brave",
        task_id=FACT,
        mode="live",
        harness_auth="account",
        binary=str(harness),
        native_search_available=False,
        external_provider=ProviderExposure(
            provider_id="brave",
            tool_name="mcp__brave__*",
            mcp_server_name="brave",
            mcp_server_config={"command": sys.executable, "args": [str(vendor)]},
        ),
        run_id_override="live-brave-metered",
        timeout_seconds=60.0,
        boot_timeout_seconds=30.0,
    )
    environ = {"PATH": os.environ.get("PATH", ""), "HOME": str(Path.home()), LIVE_ENV: "1"}

    result = run_live_harness(config, tmp_path / "runs", environ=environ)

    assert result.status == "succeeded", result
    run = json.loads((result.bundle_dir / "run.json").read_text())
    (ref,) = run["provider_call_refs"]
    record = json.loads((result.bundle_dir / ref).read_text())
    assert (record["provider_id"], record["operation"], record["response"]["pricing_tier"]) == (
        "brave",
        "brave_web_search",
        "search:request",
    )
    validate_fixture_run(result.bundle_dir)
    metadata = json.loads((result.bundle_dir / "artifacts" / "spawn-metadata.json").read_text())
    assert metadata["process"]["provider_calls"] == 1


@pytest.mark.parametrize("legacy", [False, True])
def test_the_report_prices_only_new_empty_result_records(tmp_path: Path, legacy: bool) -> None:
    root = _run_root(tmp_path)
    entries = _arm(
        root,
        "codex",
        "no-search",
        ["pass"] * 3,
        usage=_usage(1000, 0, 200, 0),
        model_id="gpt-6-sol",
    )
    entries += _arm(
        root,
        "codex",
        "brave",
        ["pass"] * 3,
        usage=_usage(1000, 0, 200, 0),
        model_id="gpt-6-sol",
        search_calls=1,
    )
    for entry in entries:
        if entry["provider_id"] != "brave":
            continue
        run_dir = Path(entry["run_dir"])
        record = _call(
            _meter(tmp_path, "brave"),
            "brave_web_search",
            {"query": "q"},
            {"isError": True, "content": [{"type": "text", "text": "No web results found"}]},
        )
        record["run_id"] = entry["run_id"]
        if legacy:
            # Historical records have only the content length, not the text.
            record["response"].pop("error_kind")
            record["response"].pop("pricing_tier")
            record["response"]["meter"] = {
                "basis": "unpriced",
                "unpriced_reason": "call_failed",
            }
        (run_dir / "provider-calls" / "c1.json").write_text(json.dumps(record))
        run = json.loads((run_dir / "run.json").read_text())
        run["provider_call_refs"] = ["provider-calls/c1.json"]
        (run_dir / "run.json").write_text(json.dumps(run))
    _state(root, entries)

    report = build_bakeoff_report(
        root, module_base=MODULE_ROOT, generated_at=GENERATED_AT, price_table=load_price_table()
    )

    runs = [run for run in report["runs"] if run["arm"] == "codex+brave"]
    (control, *_) = [run for run in report["runs"] if run["arm"] == "codex+no-search"]
    assert control["cost_usd"] is not None
    if legacy:
        assert runs and all(run["cost_usd"] is None for run in runs)
        return
    assert runs and all(run["cost_exclusions"] == [] for run in runs)
    # Same tokens and model as the control, plus one Brave request at $0.005.
    assert all(run["cost_usd"] == pytest.approx(control["cost_usd"] + 0.005) for run in runs)


def test_credits_cost_is_a_receipt_only_on_a_firecrawl_alexandria_call(tmp_path: Path) -> None:
    # A `creditsCost` field in an ordinary result is data, not a vendor receipt.
    eleven = [{"url": f"https://e.test/{i}"} for i in range(11)]
    search = _call(
        _meter(tmp_path, "firecrawl"),
        "firecrawl_search",
        {"query": "q"},
        _text({"data": {"web": eleven, "creditsCost": 99}}),
    )
    assert search["response"]["meter"]["basis"] == "tariff"
    assert search["response"]["provider_units"] == {"credits_used": 4, "credits_source": "tariff"}

    other_vendor = _call(
        _meter(tmp_path, "exa"), "web_search_exa", {"query": "q"}, _text({"creditsCost": 7})
    )
    assert "credits_used" not in other_vendor["response"].get("provider_units", {})
    assert other_vendor["response"]["meter"]["basis"] == "tariff"


@pytest.mark.parametrize("empty", [None, ""])
def test_an_empty_selector_is_absent_for_both_pricing_and_the_record(
    tmp_path: Path, empty: Any
) -> None:
    record = _call(
        _meter(tmp_path, "tavily"),
        "tavily_search",
        {"query": "q", "search_depth": empty},
        _text({"results": []}),
    )
    assert record["request"]["pricing_arguments"] == {}
    assert record["response"]["provider_units"] == {"credits_used": 1, "credits_source": "tariff"}


def test_vendor_does_not_inherit_meter_bootstrap_pythonpath(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from sew import mcp_meter

    core_path = tmp_path / "worker-core"
    original_path = tmp_path / "vendor-lib"
    sew_path = Path(mcp_meter.__file__).resolve().parents[1]
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join(map(str, [sew_path, core_path, original_path]))
    )
    monkeypatch.setenv("DEFAULT_PARAMETERS", '{"search_depth":"advanced"}')
    env = mcp_meter._vendor_environment(SimpleNamespace(import_root=str(core_path)))
    assert env["PYTHONPATH"] == str(original_path)
    assert env["DEFAULT_PARAMETERS"] == '{"search_depth":"advanced"}'
    assert os.environ["PYTHONPATH"].startswith(str(sew_path))


def test_vendor_removes_bootstrap_only_pythonpath(monkeypatch):
    from types import SimpleNamespace
    from sew import mcp_meter

    core_path = Path(__file__).resolve().parents[3] / "worker-pool/lib/python"
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(map(str, [Path(mcp_meter.__file__).resolve().parents[1], core_path])),
    )
    assert "PYTHONPATH" not in mcp_meter._vendor_environment(
        SimpleNamespace(import_root=str(core_path))
    )


def test_host_meter_receives_call_and_failure_keeps_local_record(tmp_path, monkeypatch):
    from sew import host

    seen = []

    class Host:
        def meter_provider_call(self, **call):
            seen.append(call)
            raise RuntimeError("host unavailable")

    monkeypatch.setattr(host, "get_host", lambda: Host())
    meter = _meter(tmp_path, "brave")
    meter.client_message(
        {
            "id": 1,
            "method": "tools/call",
            "params": {"name": "brave_web_search", "arguments": {"query": "q"}},
        }
    )
    meter.server_message({"id": 1, "result": _text({})})
    assert len(seen) == 1
    assert seen[0]["provider_id"] == "brave"
    assert list(meter.call_dir.glob("brave-*.json"))
