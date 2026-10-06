"""Offline contract tests for the Brave and Tavily adapters.

Response shapes are trimmed from live calls made 2026-09-25 through the Agent
OS secrets bus (see the adapter docstrings); nothing here touches the network.
"""

from __future__ import annotations

from typing import Any, Mapping

import pytest

from sew.providers import (
    PROVIDER_CREDENTIALS,
    CredentialResolver,
    HttpResponse,
    make_provider,
)

BRAVE_SEARCH = {
    "type": "search",
    "query": {"original": "pg_combinebackup"},
    "mixed": {"type": "mixed", "main": [{"type": "web", "index": 0, "all": False}]},
    "videos": {"type": "videos", "results": []},
    "web": {
        "type": "search",
        "results": [
            {
                "title": "PostgreSQL: Documentation: 17: pg_combinebackup",
                "url": "https://www.postgresql.org/docs/17/app-pgcombinebackup.html",
                "description": "<strong>pg_combinebackup</strong> reconstructs a full backup.",
                "extra_snippets": ["Incremental backups were added in 17.", ""],
                "page_age": "2024-09-26T00:00:00",
                "age": "September 26, 2024",
            },
            {
                "title": "No excerpts",
                "url": "https://example.test/plain",
                "description": "",
            },
        ],
    },
}

TAVILY_SEARCH = {
    "query": "pg_combinebackup",
    "answer": None,
    "images": [],
    "follow_up_questions": None,
    "results": [
        {
            "url": "https://www.postgresql.org/docs/17/app-pgcombinebackup.html",
            "title": "pg_combinebackup",
            "content": "pg_combinebackup reconstructs a synthetic full backup.",
            "score": 0.91,
            "raw_content": "the whole page " * 200,
        }
    ],
    "response_time": 1.13,
    "usage": {"credits": 1},
    "request_id": "4c225d75-cc44-4bab-80bd-870f2e06cfda",
}

TAVILY_EXTRACT = {
    "results": [
        {
            "url": "https://www.postgresql.org/docs/17/app-pgcombinebackup.html",
            "title": "pg_combinebackup",
            "raw_content": "full page text",
            "images": [],
        }
    ],
    "failed_results": [],
    "response_time": 0.02,
    "usage": {"credits": 1},
    "request_id": "0681190a-b4e3-4ebc-b343-da57d1d0344b",
}


class StubTransport:
    def __init__(self, body: Mapping[str, Any]) -> None:
        self.body = body
        self.calls: list[dict[str, Any]] = []

    def request(self, method, url, *, headers, json_body, timeout_seconds) -> HttpResponse:
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers), "json_body": dict(json_body)}
        )
        return HttpResponse(200, self.body)


def _provider(provider_id: str, body: Mapping[str, Any]):
    env_name = PROVIDER_CREDENTIALS[provider_id][0]
    transport = StubTransport(body)
    provider = make_provider(
        provider_id,
        credential_resolver=CredentialResolver(environ={env_name: "secret"}),
        live_enabled=True,
        transport=transport,
    )
    return provider, transport


def test_credentials_route_through_the_same_env_and_ref_contract() -> None:
    # `*_REF` values resolve through the Agent OS secrets bus exactly as the
    # other providers' do; no provider gets a private credential path.
    assert PROVIDER_CREDENTIALS["brave"] == ("SEW_BRAVE_API_KEY", "SEW_BRAVE_API_KEY_REF")
    assert PROVIDER_CREDENTIALS["tavily"] == ("SEW_TAVILY_API_KEY", "SEW_TAVILY_API_KEY_REF")
    resolver = CredentialResolver(
        environ={"SEW_TAVILY_API_KEY_REF": "env:TAVILY_KEY", "TAVILY_KEY": "tvly-x"}
    )
    assert resolver.resolve("tavily") == ("tvly-x", "SEW_TAVILY_API_KEY_REF")


def test_brave_request_shape_and_auth() -> None:
    provider, transport = _provider("brave", BRAVE_SEARCH)

    provider.search("pg_combinebackup", run_id="run", num_results=50, mode="fast")

    call = transport.calls[0]
    assert call["url"] == "https://api.search.brave.com/res/v1/web/search"
    assert call["headers"]["X-Subscription-Token"] == "secret"
    assert "Authorization" not in call["headers"]
    # `count` is capped at the API's page size; the unsupported depth knob is
    # dropped rather than forwarded as an undefined parameter.
    assert call["json_body"] == {"q": "pg_combinebackup", "count": 20, "extra_snippets": True}


def test_brave_results_unwrap_web_and_strip_markup() -> None:
    provider, _ = _provider("brave", BRAVE_SEARCH)

    result = provider.search("pg_combinebackup", run_id="run", num_results=5)

    assert result.status == "ok"
    # The generic extractor would find no top-level list and return nothing.
    assert len(result.sources) == 2
    first = result.sources[0]
    assert first["url"] == "https://www.postgresql.org/docs/17/app-pgcombinebackup.html"
    assert "<strong>" not in first["snippet"]
    assert first["snippet"].startswith("pg_combinebackup reconstructs a full backup.")
    assert "Incremental backups were added in 17." in first["snippet"]
    assert first["published_at"] == "2024-09-26T00:00:00"
    # A result with no excerpt falls back to its title rather than vanishing.
    assert result.sources[1]["snippet"] == "No excerpts"


def test_tavily_request_shape_maps_depth_and_caps_results() -> None:
    provider, transport = _provider("tavily", TAVILY_SEARCH)

    provider.search("q", run_id="run", mode="fast", num_results=40)
    provider.search("q", run_id="run", mode="advanced", num_results=5)
    provider.search("q", run_id="run", mode="fast", search_depth="advanced")

    fast, deep, pinned = (call["json_body"] for call in transport.calls)
    assert transport.calls[0]["url"] == "https://api.tavily.com/search"
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer secret"
    assert fast == {"query": "q", "search_depth": "basic", "include_usage": True, "max_results": 20}
    assert deep["search_depth"] == "advanced"
    # An explicit native value wins over the cross-vendor `mode` mapping.
    assert pinned["search_depth"] == "advanced"


def test_tavily_prefers_the_query_excerpt_and_reports_credits() -> None:
    provider, _ = _provider("tavily", TAVILY_SEARCH)

    result = provider.search("q", run_id="run")

    source = result.sources[0]
    # The whole-page raw_content must not silently replace the excerpt a
    # search caller asked for.
    assert source["snippet"] == "pg_combinebackup reconstructs a synthetic full backup."
    assert result.provider_units["credits_used"] == 1
    assert result.provider_units["request_id"] == "4c225d75-cc44-4bab-80bd-870f2e06cfda"


def test_tavily_fetch_uses_extract() -> None:
    provider, transport = _provider("tavily", TAVILY_EXTRACT)

    result = provider.fetch(
        "https://www.postgresql.org/docs/17/app-pgcombinebackup.html", run_id="r"
    )

    call = transport.calls[0]
    assert call["url"] == "https://api.tavily.com/extract"
    assert call["json_body"] == {
        "urls": ["https://www.postgresql.org/docs/17/app-pgcombinebackup.html"],
        "include_usage": True,
    }
    assert result.status == "ok"
    assert result.sources[0]["snippet"] == "full page text"


@pytest.mark.parametrize("provider_id", ["brave", "tavily"])
def test_unsupported_operations_are_not_applicable_not_errors(provider_id: str) -> None:
    provider, transport = _provider(provider_id, {})

    result = provider.crawl("https://example.test", run_id="run")

    assert result.status == "not_applicable"
    assert result.error_class == "unsupported_operation"
    assert transport.calls == []


@pytest.mark.parametrize("provider_id", ["brave", "tavily"])
def test_missing_credential_is_unavailable_without_a_network_call(provider_id: str) -> None:
    transport = StubTransport({})
    provider = make_provider(
        provider_id,
        credential_resolver=CredentialResolver(environ={}),
        live_enabled=True,
        transport=transport,
    )

    result = provider.search("q", run_id="run")

    assert result.status == "unavailable"
    assert result.error_class == "missing_credential"
    assert transport.calls == []
