"""Offline tests for the retrieval lane scorer and the corrected wire formats.

These never touch the network: provider request bodies are pure functions of a
ProviderRequest, and response normalization is a pure function of a payload, so
both can be pinned against the shapes captured from the live APIs.
"""

from __future__ import annotations

import math

import pytest

from sew.providers import (
    CredentialResolver,
    HttpResponse,
    ProviderRequest,
    make_provider,
)
from sew.retrieval_lane import (
    CONFIGS,
    aggregate,
    domain_matches,
    host_of,
    pct,
    score_cell,
    wilson,
)


class StubTransport:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.calls: list[dict] = []

    def request(self, method, url, *, headers, json_body, timeout_seconds):
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers), "body": json_body}
        )
        return self.response


def _provider(provider_id: str, body):
    env = {
        "exa": {"SEW_EXA_API_KEY": "k"},
        "parallel-web": {"SEW_PARALLEL_WEB_API_KEY": "k"},
        "firecrawl": {"SEW_FIRECRAWL_API_KEY": "k"},
    }[provider_id]
    transport = StubTransport(HttpResponse(200, body))
    provider = make_provider(
        provider_id,
        credential_resolver=CredentialResolver(environ=env),
        live_enabled=True,
        transport=transport,
    )
    return provider, transport


# --------------------------------------------------------------------------
# Wire formats. Each assertion below corresponds to a defect found by probing
# the live API on 2026-09-19; they are regression pins, not style preferences.
# --------------------------------------------------------------------------


def test_exa_search_requests_contents_or_results_carry_no_text() -> None:
    provider, transport = _provider("exa", {"results": []})
    provider.call(
        ProviderRequest(
            operation="search",
            run_id="r",
            query="q",
            options={"num_results": 5, "mode": "fast"},
        )
    )
    call = transport.calls[0]
    assert call["url"] == "https://api.exa.ai/search"
    assert call["headers"]["x-api-key"] == "k"
    assert call["body"]["query"] == "q"
    assert call["body"]["type"] == "fast"
    assert call["body"]["numResults"] == 5
    # Without `contents`, Exa returns metadata only and every snippet is empty.
    assert call["body"]["contents"] == {"highlights": True}


def test_exa_highlights_list_becomes_snippet_and_cost_is_extracted() -> None:
    provider, _ = _provider(
        "exa",
        {
            "results": [
                {
                    "url": "https://example.com/a",
                    "title": "A",
                    "highlights": ["first fragment", "second fragment"],
                    "publishedDate": "2026-01-02T00:00:00.000Z",
                }
            ],
            "costDollars": {"total": 0.007, "search": {"neural": 0.007}},
            "searchTime": 303.8,
            "requestId": "abc",
        },
    )
    result = provider.call(ProviderRequest(operation="search", run_id="r", query="q"))
    source = result.schema_sources()[0]
    assert "first fragment" in source["snippet"]
    assert "second fragment" in source["snippet"]
    assert source["published_at"] == "2026-01-02T00:00:00.000Z"
    assert result.provider_cost == {"currency": "USD", "total": 0.007, "search": {"neural": 0.007}}
    assert result.provider_units["search_time_ms"] == 303.8


def test_parallel_uses_api_key_header_and_objective_body() -> None:
    provider, transport = _provider("parallel-web", {"results": []})
    provider.call(
        ProviderRequest(
            operation="search",
            run_id="r",
            query="q",
            options={
                "num_results": 5,
                "max_chars_per_result": 4000,
                "mode": "fast",
                "advanced_settings": {"excerpt_settings": {"num_excerpts": 2}},
            },
        )
    )
    call = transport.calls[0]
    assert call["url"] == "https://api.parallel.ai/v1/search"
    # Bearer auth is rejected by Parallel; the header name is x-api-key.
    assert call["headers"]["x-api-key"] == "k"
    assert "Authorization" not in call["headers"]
    # Parallel takes an objective plus queries, not a single `query` field.
    assert call["body"]["objective"] == "q"
    assert call["body"]["search_queries"] == ["q"]
    assert call["body"]["mode"] == "fast"
    # A top-level max_results is rejected with HTTP 422; it lives under
    # advanced_settings.
    assert "max_results" not in call["body"]
    assert call["body"]["advanced_settings"]["max_results"] == 5
    assert call["body"]["advanced_settings"]["excerpt_settings"] == {
        "max_chars_per_result": 4000,
        "num_excerpts": 2,
    }


def test_parallel_excerpt_list_becomes_snippet() -> None:
    provider, _ = _provider(
        "parallel-web",
        {
            "results": [
                {
                    "url": "https://example.com/a",
                    "title": "A",
                    "excerpts": ["alpha", "beta"],
                    "publish_date": "2026-02-05",
                }
            ],
            "search_id": "search_1",
        },
    )
    result = provider.call(ProviderRequest(operation="search", run_id="r", query="q"))
    source = result.schema_sources()[0]
    assert "alpha" in source["snippet"] and "beta" in source["snippet"]
    assert source["published_at"] == "2026-02-05"
    assert result.provider_units["search_id"] == "search_1"


def test_firecrawl_targets_v2_and_unwraps_nested_data_channels() -> None:
    provider, transport = _provider(
        "firecrawl",
        {
            "success": True,
            "data": {
                "web": [
                    {"url": "https://example.com/a", "title": "A", "markdown": "page body"},
                    {"url": "https://example.com/b", "title": "B", "description": "serp text"},
                ],
                "videos": [{"url": "https://example.com/c", "title": "C", "markdown": "video"}],
            },
            "creditsUsed": 6,
            "id": "req-1",
        },
    )
    result = provider.call(
        ProviderRequest(operation="search", run_id="r", query="q", options={"num_results": 5})
    )
    call = transport.calls[0]
    assert call["url"] == "https://api.firecrawl.dev/v2/search"
    assert call["body"]["limit"] == 5
    # Without scrapeOptions.formats, search returns url/title/description only.
    assert call["body"]["scrapeOptions"]["formats"] == [{"type": "markdown"}]
    # The generic extractor sees `data` as a Mapping and yields one opaque
    # record; the v2 shape must be unwrapped to its per-channel lists.
    sources = result.schema_sources()
    assert [s["url"] for s in sources] == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]
    assert sources[0]["snippet"] == "page body"
    # A page that blocks scraping still carries SERP text; that is real signal.
    assert sources[1]["snippet"] == "serp text"
    assert result.provider_units["credits_used"] == 6


def test_firecrawl_empty_v2_search_does_not_fall_back_to_metadata_lists() -> None:
    provider, _ = _provider(
        "firecrawl",
        {
            "success": True,
            "data": {"web": []},
            "warnings": [{"url": "https://metadata.example/not-a-result"}],
        },
    )

    result = provider.call(ProviderRequest(operation="search", run_id="r", query="q"))

    assert result.status == "ok"
    assert result.sources == ()
    assert result.call_record["response"]["result_count"] == 0


def test_content_length_measures_provider_output_not_retained_excerpt() -> None:
    long_text = "x" * 5000
    provider, _ = _provider(
        "firecrawl",
        {"data": {"web": [{"url": "https://example.com/a", "title": "A", "markdown": long_text}]}},
    )
    result = provider.call(ProviderRequest(operation="search", run_id="r", query="q"))
    source = result.schema_sources()[0]
    # The stored excerpt stays bounded by the retention policy...
    assert len(source["snippet"]) == 1200
    # ...but the measurement reflects what the provider actually shipped, or the
    # context-cost comparison collapses to a tie at the cap.
    assert source["content_length"] == 5000


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def test_host_and_domain_matching_handles_subdomains_and_www() -> None:
    assert host_of("https://www.Example.com/x") == "example.com"
    assert domain_matches("https://docs.exa.ai/reference", ["exa.ai"])
    assert domain_matches("https://exa.ai/", ["exa.ai"])
    assert not domain_matches("https://notexa.ai/", ["exa.ai"])


def test_score_cell_finds_answer_and_reciprocal_rank() -> None:
    query = {
        "id": "q",
        "answer_patterns": [r"\b5432\b"],
        "gold_domains": ["postgresql.org"],
    }
    sources = [
        {"url": "https://blog.example.com/a", "title": "t", "snippet": "no number here"},
        {"url": "https://www.postgresql.org/docs/", "title": "t", "snippet": "port 5432 default"},
    ]
    scored = score_cell(query, sources)
    assert scored["answer_found"] is True
    assert scored["gold_hit"] is True
    assert scored["gold_reciprocal_rank"] == 0.5


def test_score_cell_reports_none_when_no_ground_truth_declared() -> None:
    scored = score_cell({"id": "q", "answer_patterns": [], "gold_domains": []}, [])
    # No declared truth must read as "not scored", never as a failure.
    assert scored["answer_found"] is None
    assert scored["gold_hit"] is None


def test_score_cell_gold_miss_is_zero_rank_not_none() -> None:
    scored = score_cell(
        {"id": "q", "gold_domains": ["postgresql.org"]},
        [{"url": "https://elsewhere.example/a", "title": "t", "snippet": "s"}],
    )
    assert scored["gold_hit"] is False
    assert scored["gold_reciprocal_rank"] == 0.0


def test_freshness_counts_only_sources_inside_window() -> None:
    from datetime import UTC, datetime, timedelta

    recent = (datetime.now(UTC) - timedelta(days=5)).isoformat().replace("+00:00", "Z")
    stale = (datetime.now(UTC) - timedelta(days=400)).isoformat().replace("+00:00", "Z")
    scored = score_cell(
        {"id": "q", "freshness_window_days": 120},
        [
            {"url": "https://a.example/", "title": "t", "snippet": "s", "published_at": recent},
            {"url": "https://b.example/", "title": "t", "snippet": "s", "published_at": stale},
            {"url": "https://c.example/", "title": "t", "snippet": "s", "published_at": None},
        ],
    )
    assert scored["fresh_source_count"] == 1


def test_wilson_interval_brackets_point_estimate() -> None:
    lo, hi = wilson(8, 10)
    assert lo < 0.8 < hi
    assert wilson(0, 0) == (0.0, 0.0)
    lo, hi = wilson(10, 10)
    assert math.isclose(hi, 1.0)


@pytest.mark.parametrize(
    "values,q,expected",
    [([1.0], 0.5, 1.0), ([1.0, 2.0, 3.0], 0.5, 2.0), ([1.0, 2.0, 3.0, 4.0], 0.5, 2.5)],
)
def test_percentile_interpolates(values, q, expected) -> None:
    assert pct(values, q) == expected


def test_aggregate_flags_query_no_provider_answered_as_suspect_ground_truth() -> None:
    from sew.retrieval_lane import CellResult

    catalog = {
        "catalog_id": "c",
        "queries": [
            {"id": "good", "query_class": "fact_lookup", "answer_patterns": ["x"]},
            {"id": "bad", "query_class": "fact_lookup", "answer_patterns": ["x"]},
        ],
    }
    cells = []
    for provider in ("exa", "parallel-web", "firecrawl"):
        for qid, found in (("good", True), ("bad", False)):
            cells.append(
                CellResult(
                    query_id=qid,
                    query_class="fact_lookup",
                    provider_id=provider,
                    repetition=1,
                    status="ok",
                    latency_ms=100.0,
                    result_count=5,
                    content_chars=1000,
                    answer_found=found,
                    gold_hit=None,
                    gold_reciprocal_rank=None,
                    fresh_source_count=None,
                    provider_cost_usd=None,
                )
            )
    report = aggregate(cells, catalog)
    # 'bad' is far likelier to be wrong ground truth than a three-way miss.
    assert report["suspect_ground_truth"] == ["bad"]
    # And excluding it must leave the remaining rate undistorted.
    for provider in ("exa", "parallel-web", "firecrawl"):
        summary = report["by_provider"][provider]
        assert summary["answer_n"] == 1
        assert summary["answer_rate"] == 1.0


def test_aggregate_counts_single_provider_miss_as_failure_not_suspect() -> None:
    from sew.retrieval_lane import CellResult

    catalog = {
        "catalog_id": "c",
        "queries": [{"id": "missed", "query_class": "fact_lookup", "answer_patterns": ["x"]}],
    }
    cells = [
        CellResult(
            query_id="missed",
            query_class="fact_lookup",
            provider_id="exa",
            repetition=1,
            status="ok",
            latency_ms=100.0,
            result_count=5,
            content_chars=1000,
            answer_found=False,
            gold_hit=None,
            gold_reciprocal_rank=None,
            fresh_source_count=None,
            provider_cost_usd=None,
        )
    ]

    report = aggregate(cells, catalog)

    assert report["suspect_ground_truth"] == []
    assert report["by_provider"]["exa"]["answer_n"] == 1
    assert report["by_provider"]["exa"]["answer_rate"] == 0.0


def test_matched_config_pins_retrieval_depth_for_every_provider() -> None:
    matched = CONFIGS["matched"]
    # Parallel defaults to `advanced` (~3s); leaving depth unpinned would make
    # the latency table a measurement of defaults rather than of capability.
    assert matched["parallel-web"]["mode"] == "fast"
    assert matched["exa"]["mode"] == "fast"
    assert all("num_results" in opts for opts in matched.values())


def test_one_malformed_source_does_not_discard_the_whole_result_set() -> None:
    provider, _ = _provider(
        "firecrawl",
        {
            "data": {
                "web": [
                    {"url": "https://good.example/a", "title": "A", "markdown": "alpha"},
                    {"url": "javascript:alert(1)", "title": "B", "markdown": "beta"},
                    {"url": "http://plain.example/c", "title": "C", "markdown": "gamma"},
                ]
            }
        },
    )
    result = provider.call(ProviderRequest(operation="search", run_id="r", query="q"))
    urls = [s["url"] for s in result.schema_sources()]
    # The unrepresentable scheme is dropped and counted; http is recorded as-is.
    assert urls == ["https://good.example/a", "http://plain.example/c"]
    assert result.call_record["response"]["dropped_source_count"] == 1
    assert result.call_record["response"]["result_count"] == 2


def test_exa_defaults_to_query_relevant_highlights_not_positional_text() -> None:
    provider, transport = _provider("exa", {"results": []})
    # A character budget alone must NOT flip Exa to its positional `text` view:
    # `text` returns the page from the top, so a value deep in a docs page falls
    # outside the budget and the answer rate drops for reasons that have nothing
    # to do with index quality.
    provider.call(
        ProviderRequest(
            operation="search",
            run_id="r",
            query="q",
            options={"max_chars_per_result": 4000},
        )
    )
    assert transport.calls[0]["body"]["contents"] == {"highlights": True}


def test_exa_text_view_is_opt_in_and_honours_char_budget() -> None:
    provider, transport = _provider("exa", {"results": []})
    provider.call(
        ProviderRequest(
            operation="search",
            run_id="r",
            query="q",
            options={"content_view": "text", "max_chars_per_result": 4000},
        )
    )
    assert transport.calls[0]["body"]["contents"] == {"text": {"maxCharacters": 4000}}


def test_exa_text_view_control_config_differs_only_in_content_view() -> None:
    matched, control = CONFIGS["matched"], CONFIGS["exa-text-view"]
    assert matched["exa"]["content_view"] == "highlights"
    assert control["exa"]["content_view"] == "text"
    # Everything else must match, or the delta is not attributable to the view.
    assert matched["exa"]["mode"] == control["exa"]["mode"]
    assert matched["exa"]["num_results"] == control["exa"]["num_results"]


def test_all_sources_dropped_degrades_the_call_instead_of_reporting_clean_ok() -> None:
    # Adversarial-review finding (PR #6901): per-source tolerance meant that if
    # EVERY extracted item failed normalization -- an adapter bug, or a provider
    # changing its response shape -- the call still returned `ok` with zero
    # results. Downstream that reads as weak provider retrieval rather than as
    # our own defect, which is the exact misattribution this lane exists to
    # avoid.
    provider, _ = _provider(
        "firecrawl",
        {
            "data": {
                "web": [
                    {"url": "javascript:alert(1)", "title": "A"},
                    {"url": "file:///etc/passwd", "title": "B"},
                ]
            }
        },
    )
    result = provider.call(ProviderRequest(operation="search", run_id="r", query="q"))
    assert result.status == "failed"
    assert result.error_class == "source_normalization_failed"
    assert result.call_record["response"]["dropped_source_count"] == 2
    assert result.call_record["response"]["result_count"] == 0


def test_partial_source_drop_stays_ok_but_carries_a_parse_warning() -> None:
    provider, _ = _provider(
        "firecrawl",
        {
            "data": {
                "web": [
                    {"url": "https://good.example/a", "title": "A", "markdown": "x"},
                    {"url": "javascript:alert(1)", "title": "B"},
                ]
            }
        },
    )
    result = provider.call(ProviderRequest(operation="search", run_id="r", query="q"))
    assert result.status == "ok"
    assert result.call_record["response"]["parse_warning"] == "source_normalization_partial"


def test_empty_result_set_is_not_marked_a_parse_failure() -> None:
    # Zero results with zero drops is an empty search, not an adapter bug.
    provider, _ = _provider("firecrawl", {"data": {"web": []}})
    result = provider.call(ProviderRequest(operation="search", run_id="r", query="q"))
    assert result.status == "ok"
    assert "parse_warning" not in result.call_record["response"]
