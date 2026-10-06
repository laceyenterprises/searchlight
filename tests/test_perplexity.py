"""Offline contract tests for Perplexity: the Search API adapter and the Agent
API arm. Shapes are trimmed from live calls made 2026-09-25 through the Agent
OS secrets bus; nothing here touches the network.
"""

from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message
from typing import Any, Mapping

import pytest

from sew import agent_lane
from sew.agent_lane import AGENT_ARMS, TIERS, run_perplexity_agent
from sew.providers import PROVIDER_CREDENTIALS, CredentialResolver, HttpResponse, make_provider

SEARCH = {
    "id": "e38104d5-6bd7-4d82-bc4e-0a21179d1f77",
    "results": [
        {
            "title": "pg_combinebackup",
            "url": "https://www.postgresql.org/docs/17/app-pgcombinebackup.html",
            "snippet": "pg_combinebackup reconstructs a synthetic full backup.",
            "date": "2024-09-26",
            "last_updated": "2025-08-12",
        }
    ],
}

AGENT_OK = {
    "id": "resp_1",
    "object": "response",
    "status": "completed",
    "model": "openai/gpt-6-luna",
    "output": [
        {"type": "search_results", "queries": ["a", "b"], "results": [{"id": 1}] * 15},
        {
            "type": "message",
            "id": "msg_1",
            "status": "completed",
            "role": "assistant",
            "content": [
                {"type": "output_text", "text": '{"release_version": "17", ', "annotations": []},
                {"type": "output_text", "text": '"tool_name": "pg_combinebackup"}'},
            ],
        },
    ],
    "usage": {
        "input_tokens": 900,
        "output_tokens": 40,
        "total_tokens": 940,
        "cost": {"currency": "USD", "input_cost": 0, "output_cost": 5e-05, "total_cost": 0.00302},
    },
}

TASK = {
    "id": "pg-incremental-backup",
    "prompt": "Which version introduced incremental backups?",
    "output_schema": {
        "type": "object",
        "properties": {"release_version": {"type": "string"}, "tool_name": {"type": "string"}},
        "required": ["release_version", "tool_name"],
    },
}


class StubTransport:
    def __init__(self, body: Mapping[str, Any]) -> None:
        self.body = body
        self.calls: list[dict[str, Any]] = []

    def request(self, method, url, *, headers, json_body, timeout_seconds) -> HttpResponse:
        self.calls.append({"url": url, "headers": dict(headers), "json_body": dict(json_body)})
        return HttpResponse(200, self.body)


def _search_provider():
    transport = StubTransport(SEARCH)
    provider = make_provider(
        "perplexity",
        credential_resolver=CredentialResolver(environ={"SEW_PERPLEXITY_API_KEY": "secret"}),
        live_enabled=True,
        transport=transport,
    )
    return provider, transport


# --- Search API adapter ------------------------------------------------------


def test_one_key_serves_both_apis_through_the_bus_contract() -> None:
    assert PROVIDER_CREDENTIALS["perplexity"] == (
        "SEW_PERPLEXITY_API_KEY",
        "SEW_PERPLEXITY_API_KEY_REF",
    )


def test_search_request_maps_mode_results_and_page_budget() -> None:
    provider, transport = _search_provider()

    provider.search("q", run_id="r", mode="fast", num_results=50, max_chars_per_result=4000)
    provider.search("q", run_id="r", num_results=5)
    provider.search(
        "q",
        run_id="r",
        mode="fast",
        search_type="web",
        search_context_size="low",
        max_chars_per_result=4000,
    )

    fast, default, pinned = (call["json_body"] for call in transport.calls)
    assert transport.calls[0]["url"] == "https://api.perplexity.ai/search"
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer secret"
    assert fast == {
        "query": "q",
        "search_type": "fast",
        "max_results": 20,
        "max_tokens_per_page": 1000,
    }
    assert default == {"query": "q", "search_type": "web", "max_results": 5}
    # The docs say not to send a page-token budget alongside search_context_size.
    assert pinned["search_type"] == "web"
    assert pinned["search_context_size"] == "low"
    assert "max_tokens_per_page" not in pinned


def test_search_results_normalize_dates() -> None:
    provider, _ = _search_provider()

    result = provider.search("q", run_id="r")

    assert result.status == "ok"
    source = result.sources[0]
    assert source["snippet"] == "pg_combinebackup reconstructs a synthetic full backup."
    assert source["published_at"] == "2024-09-26"
    assert source["modified_at"] == "2025-08-12"


# --- Agent API arm -----------------------------------------------------------


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, Any]] = []

    def fake_post(url, headers, body, timeout, **kwargs):
        calls.append({"url": url, "headers": dict(headers), "body": body})
        return 200, fake_post.response

    fake_post.response = AGENT_OK
    monkeypatch.setattr(agent_lane, "_credential", lambda arm: f"key-{arm}")
    monkeypatch.setattr(agent_lane, "_post", fake_post)
    return calls, fake_post


def test_perplexity_agent_is_a_tiered_arm() -> None:
    assert "perplexity-agent" in AGENT_ARMS
    # Deepest preset whose median cost stays within each tier.
    assert TIERS["low"]["perplexity-agent"]["preset"] == "medium"
    assert TIERS["mid"]["perplexity-agent"]["preset"] == "high"


def test_agent_request_uses_preset_and_json_schema_without_overriding_tools(captured) -> None:
    calls, _ = captured

    run_perplexity_agent(TASK, TIERS["low"]["perplexity-agent"], 60.0)

    call = calls[0]
    assert call["url"] == "https://api.perplexity.ai/v1/agent"
    assert call["headers"]["Authorization"] == "Bearer key-perplexity-agent"
    body = call["body"]
    assert body["preset"] == "medium"
    assert body["input"] == TASK["prompt"]
    # Sending `tools` would replace the preset's own web tools.
    assert "tools" not in body
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == "sew_pg_incremental_backup"
    assert fmt["json_schema"]["schema"]["additionalProperties"] is False


def test_agent_schema_name_is_sanitized_and_capped(captured) -> None:
    calls, _ = captured
    task = dict(TASK, id="pg.backup/v17 " + "x" * 80)

    run_perplexity_agent(task, TIERS["low"]["perplexity-agent"], 60.0)

    name = calls[0]["body"]["response_format"]["json_schema"]["name"]
    assert name.startswith("sew_pg_backup_v17_x")
    assert len(name) == 64
    assert all(ch.isalnum() or ch == "_" for ch in name)


def test_agent_assembles_output_text_and_reads_reported_cost(captured) -> None:
    result = run_perplexity_agent(TASK, TIERS["low"]["perplexity-agent"], 60.0)

    assert result["status"] == "ok"
    # output_text is an SDK convenience; the wire has only content parts.
    assert result["answer"] == {"release_version": "17", "tool_name": "pg_combinebackup"}
    assert result["cost_usd"] == 0.00302
    assert result["units"]["model"] == "openai/gpt-6-luna"
    assert result["units"]["search_results_retrieved"] == 15


def test_retrieved_results_are_not_counted_as_citations(captured) -> None:
    # Fifteen pages read, no URL annotations: that is "not reported", not 15.
    result = run_perplexity_agent(TASK, TIERS["low"]["perplexity-agent"], 60.0)
    assert result["citation_count"] is None


def test_url_annotations_are_counted_as_citations(captured) -> None:
    _, fake_post = captured
    body = json.loads(json.dumps(AGENT_OK))
    body["output"][1]["content"][0]["annotations"] = [
        {"type": "url_citation", "url": "https://a.test"},
        {"type": "url_citation", "url": "https://a.test"},
        {"type": "url_citation", "url": "https://b.test"},
    ]
    fake_post.response = body

    result = run_perplexity_agent(TASK, TIERS["low"]["perplexity-agent"], 60.0)

    assert result["citation_count"] == 2


def test_unparseable_structured_output_keeps_the_text(captured) -> None:
    _, fake_post = captured
    body = json.loads(json.dumps(AGENT_OK))
    body["output"][1]["content"] = [{"type": "output_text", "text": "It was version 17."}]
    fake_post.response = body

    result = run_perplexity_agent(TASK, TIERS["low"]["perplexity-agent"], 60.0)

    assert result["status"] == "ok"
    assert result["answer"] is None
    assert result["text"] == "It was version 17."


@pytest.mark.parametrize("status", ["failed", "incomplete", "cancelled"])
def test_a_run_that_did_not_complete_is_a_failure(captured, status: str) -> None:
    _, fake_post = captured
    fake_post.response = {**AGENT_OK, "status": status}

    result = run_perplexity_agent(TASK, TIERS["low"]["perplexity-agent"], 60.0)

    assert result == {"status": "failed", "error_class": f"run_{status}"}


# --- Retry-After -------------------------------------------------------------


def _http_429(retry_after: str | None) -> urllib.error.HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        "https://x.test", 429, "Too Many", headers, io.BytesIO(b"slow down")
    )


@pytest.mark.parametrize(("retry_after", "expected_sleep"), [("30", 30.0), ("0", 8.0)])
def test_post_honours_retry_after_as_the_floor(
    monkeypatch: pytest.MonkeyPatch, retry_after: str, expected_sleep: float
) -> None:
    attempts = iter([_http_429(retry_after), None])
    sleeps: list[float] = []

    class Resp(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout):
        err = next(attempts)
        if err is not None:
            raise err
        return Resp(b'{"ok": true}')

    monkeypatch.setattr(agent_lane.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(agent_lane.time, "sleep", sleeps.append)
    monkeypatch.setattr(agent_lane, "_rate_limit_delay", lambda *a, **k: 8.0)

    assert agent_lane._post("https://x.test", {}, {}, 5.0) == (200, {"ok": True})
    assert sleeps == [expected_sleep]


@pytest.mark.parametrize(
    ("header", "expected"),
    [("12", 12.0), ("600", 600.0), ("soon", None), ("-1", None), (None, None)],
)
def test_retry_after_parsing_is_tolerant(header, expected) -> None:
    headers = Message()
    if header is not None:
        headers["Retry-After"] = header
    assert agent_lane._retry_after_seconds(headers) == expected


def test_post_gives_up_when_retry_after_exceeds_the_cell(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    sleeps: list[float] = []

    def fake_urlopen(req, timeout):
        calls.append(req)
        raise _http_429("3600")

    monkeypatch.setattr(agent_lane.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(agent_lane.time, "sleep", sleeps.append)

    status, body = agent_lane._post("https://x.test", {}, {}, 5.0, retries=3)
    assert status == 429
    assert "slow down" in body
    assert len(calls) == 1
    assert sleeps == []


def test_poll_timeout_never_outlasts_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_lane.time, "monotonic", lambda: 100.0)
    assert agent_lane._poll_timeout(110.0) == 10.0
    assert agent_lane._poll_timeout(1000.0) == 60.0
    assert agent_lane._poll_timeout(99.0) == 1.0
