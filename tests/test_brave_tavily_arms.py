"""Offline tests for the Brave Answers and Tavily Research agent-lane arms.

Shapes are trimmed from live calls made 2026-09-25 through the Agent OS secrets
bus on the catalog task `firecrawl-company`; nothing here touches the network.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from sew import agent_lane
from sew.agent_lane import (
    AGENT_ARMS,
    BRAVE_ANSWERS_USD_PER_REQUEST,
    BRAVE_ANSWERS_USD_PER_TOKEN,
    TAVILY_CREDIT_USD,
    TIERS,
    run_brave_answers,
    run_tavily_research,
)
from sew.providers import PROVIDER_CREDENTIALS, CredentialResolver

TASK = {
    "id": "firecrawl-company",
    "prompt": "When was Firecrawl founded, and by whom?",
    "output_schema": {
        "type": "object",
        "properties": {
            "founders": {"type": "array", "items": {"type": "string"}, "description": "names"},
            "founded_year": {"type": "integer", "description": "year"},
        },
        "required": ["founders", "founded_year"],
    },
}

BRAVE_OK = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "model": "brave",
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": '{"founders": ["Caleb Peffer"], "founded_year": 2024}',
            }
        }
    ],
    "usage": {"prompt_tokens": 4624, "completion_tokens": 52, "total_tokens": 4676},
}


@pytest.fixture
def post(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, Any]] = []

    def fake_post(url, headers, body, timeout, **kwargs):
        calls.append({"url": url, "headers": dict(headers), "body": body})
        return fake_post.status, fake_post.response

    fake_post.status = 200
    fake_post.response = BRAVE_OK
    monkeypatch.setattr(agent_lane, "_credential", lambda arm: f"key-{arm}")
    monkeypatch.setattr(agent_lane, "_post", fake_post)
    monkeypatch.setattr(agent_lane.time, "sleep", lambda seconds: None)
    return calls, fake_post


def test_both_arms_are_tiered_and_brave_answers_has_its_own_key() -> None:
    assert {"brave-answers", "tavily-research"} <= set(AGENT_ARMS)
    assert TIERS["low"]["tavily-research"]["model"] == "mini"
    assert TIERS["mid"]["tavily-research"]["model"] == "pro"
    # The documented per-request floor at pay-as-you-go pricing.
    assert TIERS["low"]["tavily-research"]["list_price_usd"] == 4 * TAVILY_CREDIT_USD
    # Plan-scoped keys: the Answers arm must not reuse the Search key.
    assert PROVIDER_CREDENTIALS["brave-answers"] == (
        "SEW_BRAVE_ANSWERS_API_KEY",
        "SEW_BRAVE_ANSWERS_API_KEY_REF",
    )
    resolver = CredentialResolver(environ={"SEW_BRAVE_API_KEY": "search-key"})
    with pytest.raises(Exception):
        resolver.resolve("brave-answers")


# --- Brave Answers -----------------------------------------------------------


def test_brave_request_carries_the_schema_in_the_prompt(post) -> None:
    calls, _ = post

    run_brave_answers(TASK, TIERS["mid"]["brave-answers"], 60.0)

    call = calls[0]
    assert call["url"] == "https://api.search.brave.com/res/v1/chat/completions"
    assert call["headers"] == {"X-Subscription-Token": "key-brave-answers"}
    body = call["body"]
    assert body["model"] == "brave"
    assert body["stream"] is False
    assert body["web_search_options"] == {"search_context_size": "high"}
    (message,) = body["messages"]
    assert message["role"] == "user"
    assert TASK["prompt"] in message["content"]
    assert '"founded_year"' in message["content"]


def test_brave_spend_is_computed_from_reported_tokens(post) -> None:
    result = run_brave_answers(TASK, TIERS["low"]["brave-answers"], 60.0)

    assert result["status"] == "ok"
    assert result["answer"] == {"founders": ["Caleb Peffer"], "founded_year": 2024}
    assert result["cost_usd"] == pytest.approx(
        BRAVE_ANSWERS_USD_PER_REQUEST + 4676 * BRAVE_ANSWERS_USD_PER_TOKEN
    )
    # Blocking mode offers no citations; None means "not offered", not zero.
    assert result["citation_count"] is None


def test_brave_reply_in_a_code_fence_still_parses(post) -> None:
    _, fake_post = post
    reply = '```json\n{"founders": ["A"], "founded_year": 2024}\n```'
    fake_post.response = {**BRAVE_OK, "choices": [{"message": {"content": reply}}]}

    result = run_brave_answers(TASK, TIERS["low"]["brave-answers"], 60.0)

    assert result["answer"] == {"founders": ["A"], "founded_year": 2024}


def test_brave_prose_reply_is_kept_as_text_not_crashed(post) -> None:
    _, fake_post = post
    fake_post.response = {**BRAVE_OK, "choices": [{"message": {"content": "Founded in 2024."}}]}

    result = run_brave_answers(TASK, TIERS["low"]["brave-answers"], 60.0)

    assert result["status"] == "ok"
    assert result["answer"] is None
    assert result["text"] == "Founded in 2024."


def test_brave_without_usage_reports_no_cost(post) -> None:
    _, fake_post = post
    fake_post.response = {k: v for k, v in BRAVE_OK.items() if k != "usage"}

    assert run_brave_answers(TASK, TIERS["low"]["brave-answers"], 60.0)["cost_usd"] is None


def test_brave_http_error_is_a_failure(post) -> None:
    _, fake_post = post
    fake_post.status, fake_post.response = 400, "option not in plan"

    result = run_brave_answers(TASK, TIERS["low"]["brave-answers"], 60.0)

    assert result["status"] == "failed"
    assert result["error_class"] == "create_400"


# --- Tavily Research ---------------------------------------------------------


@pytest.fixture
def tavily(post, monkeypatch: pytest.MonkeyPatch):
    calls, fake_post = post
    fake_post.response = {"request_id": "req-1", "status": "pending", "model": "mini"}
    polls: list[str] = []
    replies = iter(
        [
            (200, {"status": "processing"}),
            (
                200,
                {
                    "status": "completed",
                    "content": {"founders": ["Caleb Peffer"], "founded_year": 2024},
                    "sources": [
                        {"url": "https://a.test", "title": "A", "favicon": ""},
                        {"url": "https://a.test", "title": "A again"},
                        {"url": "https://b.test", "title": "B"},
                    ],
                    "response_time": 15.4,
                },
            ),
        ]
    )

    def fake_get(url, headers, timeout):
        polls.append(url)
        return next(replies)

    monkeypatch.setattr(agent_lane, "_get", fake_get)
    return calls, polls


def test_tavily_sends_only_properties_and_required(tavily) -> None:
    calls, _ = tavily

    run_tavily_research(TASK, TIERS["low"]["tavily-research"], 60.0)

    body = calls[0]["body"]
    assert calls[0]["url"] == "https://api.tavily.com/research"
    assert body["model"] == "mini"
    assert body["input"] == TASK["prompt"]
    # A top-level `type` is a 400 on Tavily; nested definitions keep theirs.
    assert set(body["output_schema"]) == {"properties", "required"}
    assert body["output_schema"]["properties"]["founded_year"]["type"] == "integer"


def test_tavily_polls_to_completion_and_counts_unique_cited_sources(tavily) -> None:
    _, polls = tavily

    result = run_tavily_research(TASK, TIERS["mid"]["tavily-research"], 60.0)

    assert polls == ["https://api.tavily.com/research/req-1"] * 2
    assert result["status"] == "ok"
    assert result["answer"] == {"founders": ["Caleb Peffer"], "founded_year": 2024}
    assert result["citation_count"] == 2
    # Dynamic pricing, nothing reported: an invented figure is worse than none.
    assert result["cost_usd"] is None


def test_tavily_failed_run_is_a_failure(post, monkeypatch: pytest.MonkeyPatch) -> None:
    _, fake_post = post
    fake_post.response = {"request_id": "req-2", "status": "pending"}
    monkeypatch.setattr(agent_lane, "_get", lambda *a: (200, {"status": "failed"}))

    result = run_tavily_research(TASK, TIERS["low"]["tavily-research"], 60.0)

    assert result == {"status": "failed", "error_class": "run_failed"}


def test_tavily_create_rejection_is_a_failure(post) -> None:
    _, fake_post = post
    fake_post.status = 400
    fake_post.response = {"detail": {"error": "Output schema contains unexpected keys: type."}}

    result = run_tavily_research(TASK, TIERS["low"]["tavily-research"], 60.0)

    assert result["status"] == "failed"
    assert result["error_class"] == "create_400"


def test_tavily_post_time_consumes_poll_budget(post, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]

    def slow_post(*args, **kwargs):
        clock[0] += 6.0
        return 202, {"request_id": "req-delayed"}

    monkeypatch.setattr(agent_lane, "_post", slow_post)
    monkeypatch.setattr(
        agent_lane,
        "time",
        SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda seconds: pytest.fail("late poll")),
    )
    monkeypatch.setattr(agent_lane, "_get", lambda *args: pytest.fail("late poll"))

    result = run_tavily_research(TASK, TIERS["low"]["tavily-research"], 5.0)

    assert result == {"status": "timeout", "error_class": "poll_timeout"}
