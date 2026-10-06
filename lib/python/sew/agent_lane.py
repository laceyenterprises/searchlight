"""L2 agent lane: vendor research agents, head to head, against a DIY control.

The L1 retrieval lane compares search endpoints. This lane compares the tier
above it -- Exa Agent, Parallel Task, and Firecrawl Agent -- where the vendor
runs the multi-step research server-side and returns a structured answer.

The question this lane exists to answer is not "which agent is best" but
**what does delegating actually buy**, in two currencies:

* **accuracy**, scored field-by-field against declared ground truth rather than
  as one opaque pass/fail, because "wrong value" and "missing field" are
  different failures with different fixes;
* **tokens**, which is meaningless without a denominator. A vendor agent that
  returns 400 tokens is only impressive against the cost of doing the same job
  yourself. So this lane always runs a DIY control arm and reports the ratio.

The control arm is deliberately generous to DIY: it issues ONE search and
counts what that single response would put in context. Real DIY research on a
multi-field task takes several searches plus synthesis, so the measured
reduction is a **lower bound** -- the real saving is larger than reported.

Cost parity is by PRICE, not by tier name. Exa `medium` and Parallel `pro` are
both $0.100/run; comparing vendor-default tiers would repeat the L1 mistake of
measuring configuration rather than capability.
"""

from __future__ import annotations

import json
import re
import statistics
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .catalog import module_root
from .providers import CredentialResolver, ProviderRequest, make_provider
from .retrieval_lane import pct, wilson

AGENT_ARMS = (
    "exa-agent",
    "parallel-task",
    "firecrawl-agent",
    "perplexity-agent",
    "brave-answers",
    "tavily-research",
)
CONTROL_ARM = "control-diy"

# Cost-matched tiers. The dollar figure is the vendor's published per-run price
# at that setting; `firecrawl-agent` is credit-metered, so its cap is converted
# at CREDIT_USD and its ACTUAL spend is read back from creditsUsed.
CREDIT_USD = 0.00083  # Firecrawl standard-plan approximation; recorded, not asserted.
TAVILY_CREDIT_USD = 0.008  # Tavily pay-as-you-go per credit (docs.tavily.com, 2026-09-25).
# Brave Answers list price (brave.com/search/api, 2026-09-25).
BRAVE_ANSWERS_USD_PER_REQUEST = 0.004
BRAVE_ANSWERS_USD_PER_TOKEN = 5.0 / 1_000_000

TIERS: dict[str, dict[str, Any]] = {
    "low": {
        "exa-agent": {"effort": "low", "list_price_usd": 0.025},
        "parallel-task": {"processor": "core", "list_price_usd": 0.025},
        "firecrawl-agent": {"max_credits": 30, "list_price_usd": 30 * CREDIT_USD},
        # Perplexity prices by tokens plus tool calls, so no preset has a flat
        # per-run price. Each tier takes the deepest preset whose MEDIAN cost
        # (docs.perplexity.ai pricing page's own per-preset usage figures,
        # 2026-09-25) does not exceed the tier; the list figure is that
        # estimate, and the actual spend is read back from usage.cost.
        "perplexity-agent": {"preset": "medium", "list_price_usd": 0.008},
        # Brave Answers: $4/1K requests + $5/1M tokens (brave.com/search/api,
        # 2026-09-25). Tokens are reported, so actual spend is computed exactly;
        # a live single-search run on the catalog cost ~$0.027. Research mode
        # requires SSE streaming and is not wired here, so both tiers run the
        # blocking single-search mode and `mid` widens its search context.
        "brave-answers": {"search_context_size": "medium", "list_price_usd": 0.025},
        # Tavily Research prices dynamically per request (mini 4-110 credits,
        # pro 15-250; docs.tavily.com api-credits, 2026-09-25) and reports no
        # spend, so the list figure is the documented FLOOR at pay-as-you-go
        # $0.008/credit and cost_usd stays unset.
        "tavily-research": {"model": "mini", "list_price_usd": 4 * TAVILY_CREDIT_USD},
    },
    "mid": {
        # True price parity: both exactly $0.100/run.
        "exa-agent": {"effort": "medium", "list_price_usd": 0.100},
        "parallel-task": {"processor": "pro", "list_price_usd": 0.100},
        "firecrawl-agent": {"max_credits": 120, "list_price_usd": 120 * CREDIT_USD},
        "perplexity-agent": {"preset": "high", "list_price_usd": 0.065},
        "brave-answers": {"search_context_size": "high", "list_price_usd": 0.025},
        "tavily-research": {"model": "pro", "list_price_usd": 15 * TAVILY_CREDIT_USD},
    },
}

CHARS_PER_TOKEN = 4.0  # Standard approximation; token counts are labelled est.


class AgentLaneError(RuntimeError):
    """Raised when the agent lane cannot run as configured."""


@dataclass
class ArmResult:
    task_id: str
    task_class: str
    arm: str
    tier: str
    repetition: int
    status: str
    latency_ms: float
    fields_scored: int = 0
    fields_correct: int = 0
    fields_available: int = 0
    field_detail: dict[str, Any] = field(default_factory=dict)
    schema_valid: bool | None = None
    citation_count: int | None = None
    vendor_cost_usd: float | None = None
    vendor_units: dict[str, Any] = field(default_factory=dict)
    answer_chars: int = 0
    answer_tokens_est: int = 0
    error_class: str | None = None
    raw_answer: Any = None
    started_at: str = ""


# A 429 is a fact about this account's plan tier and the runner's pacing, not
# about research quality. Booking it as a task failure would let request
# scheduling contaminate the accuracy table, so creates back off and retry;
# only a limit that survives every retry is recorded as an error.
RATE_LIMIT_RETRIES = 4
RATE_LIMIT_BACKOFF_SECONDS = 8.0
PARALLEL_RESULT_FETCH_RETRIES = 2
PARALLEL_RESULT_FETCH_BACKOFF_SECONDS = 1.0


def _rate_limit_delay(attempt: int, *, base_seconds: float, retries: int) -> float:
    from .backoff import bounded_exponential_delay

    max_attempt = max(retries - 1, 0)
    return bounded_exponential_delay(
        attempt,
        base_seconds=base_seconds,
        max_seconds=base_seconds * (2**max_attempt),
    )


def _post(
    url: str,
    headers: Mapping[str, str],
    body: Any,
    timeout: float,
    *,
    retries: int = RATE_LIMIT_RETRIES,
) -> tuple[int | str, Any]:
    data = json.dumps(body).encode()
    last: tuple[int | str, Any] = ("ERR", "no attempt")
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json", **headers}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            last = (exc.code, exc.read()[:800].decode("utf-8", "replace"))
            if exc.code != 429 or attempt == retries:
                return last
            retry_after = _retry_after_seconds(exc.headers)
            if retry_after is not None and retry_after > RETRY_AFTER_MAX_SECONDS:
                # The provider's window outlasts what a cell can wait; retrying
                # early would only hit a request it already refused.
                return last
        except Exception as exc:  # noqa: BLE001 - transport failure is data
            return "ERR", f"{type(exc).__name__}: {exc}"
        backoff = _rate_limit_delay(
            attempt, base_seconds=RATE_LIMIT_BACKOFF_SECONDS, retries=retries
        )
        # Keep local backoff as a floor even for Retry-After: 0, so repeated
        # zero hints cannot spin through the request budget immediately.
        time.sleep(max(backoff, retry_after if retry_after is not None else 0.0))
    return last


# A Retry-After longer than this is not a pacing hint the lane can honour
# inside one cell; the POST gives up at once and the cell records the 429.
RETRY_AFTER_MAX_SECONDS = 60.0


def _retry_after_seconds(headers: Any) -> float | None:
    """Seconds from a delta-seconds ``Retry-After`` header (unclamped); else None."""
    try:
        raw = headers.get("Retry-After") if headers is not None else None
    except Exception:  # noqa: BLE001 - headers are advisory
        return None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return value


def _poll_timeout(deadline: float) -> float:
    """Socket timeout for one poll: never past the cell deadline, capped at 60s."""
    return min(60.0, max(1.0, deadline - time.monotonic()))


def _get(url: str, headers: Mapping[str, str], timeout: float) -> tuple[int | str, Any]:
    req = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # A rate-limited poll is not a terminal run state; the caller's loop
        # simply waits and asks again.
        if exc.code == 429:
            time.sleep(RATE_LIMIT_BACKOFF_SECONDS)
        return exc.code, exc.read()[:800].decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return "ERR", f"{type(exc).__name__}: {exc}"


def _terminal_poll_error(code: int | str, body: Any) -> dict[str, Any] | None:
    if isinstance(code, int) and code not in (200, 202, 429):
        return {
            "status": "failed",
            "error_class": f"poll_{code}",
            "detail": str(body)[:300],
        }
    return None


def _get_parallel_result(
    run_id: Any, headers: Mapping[str, str], deadline: float
) -> tuple[int | str, Any]:
    last: tuple[int | str, Any] = ("ERR", "no attempt")
    for attempt in range(PARALLEL_RESULT_FETCH_RETRIES + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        last = _get(
            f"https://api.parallel.ai/v1/tasks/runs/{run_id}/result",
            headers,
            min(120, max(1, remaining)),
        )
        if last[0] != "ERR":
            return last
        if attempt < PARALLEL_RESULT_FETCH_RETRIES:
            time.sleep(min(PARALLEL_RESULT_FETCH_BACKOFF_SECONDS, max(0, remaining)))
    return last


def _credential(arm: str) -> str:
    provider = {
        "exa-agent": "exa",
        "parallel-task": "parallel-web",
        "firecrawl-agent": "firecrawl",
        "perplexity-agent": "perplexity",
        "brave-answers": "brave-answers",
        "tavily-research": "tavily",
    }[arm]
    value, _ = CredentialResolver().resolve(provider)
    return value


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def score_answer(task: Mapping[str, Any], answer: Any) -> dict[str, Any]:
    """Score one arm's structured answer field-by-field against ground truth.

    Scoring is per field rather than whole-answer because the two ways an arm
    can fail -- returning a wrong value and omitting the field entirely -- call
    for different fixes and must stay distinguishable in the report.
    """
    detail: dict[str, Any] = {}
    scored = correct = 0
    obj = answer if isinstance(answer, Mapping) else {}
    whole = _stringify(answer)

    # Two different bars are scored here, and conflating them would flatter the
    # vendor arms:
    #   extracted  the field came back as a named value AND that value is right
    #   available  the right value appears ANYWHERE in what the arm returned
    # A vendor agent must clear `extracted`. The DIY control returns raw search
    # text with no structure, so it can only ever clear `available` -- scoring
    # it on `extracted` would report a structural 0% and manufacture the
    # conclusion. The control is therefore given the easier bar on purpose.
    available = 0
    for spec in task.get("fields", []):
        name = spec["name"]
        patterns = [re.compile(p, re.IGNORECASE) for p in spec.get("patterns", [])]
        present = name in obj and obj[name] not in (None, "", [], {})
        value = _stringify(obj.get(name)) if present else ""
        in_field = any(p.search(value) for p in patterns) if (patterns and present) else False
        # Availability is checked against the whole payload: an arm that buries
        # the value under another key did find it, and that is worth recording
        # separately from extraction, which requires the requested field name.
        in_whole = any(p.search(whole) for p in patterns) if patterns else False

        if spec.get("optional") and not present and not in_whole:
            detail[name] = {"state": "absent_optional", "correct": None, "available": False}
            continue
        scored += 1
        if in_whole:
            available += 1
        if not present:
            detail[name] = {
                "state": "available_unextracted" if in_whole else "missing",
                "correct": False,
                "available": in_whole,
            }
        elif in_field:
            correct += 1
            detail[name] = {
                "state": "correct",
                "correct": True,
                "available": True,
                "value": value[:160],
            }
        else:
            # The requested field is present and WRONG. It does not become
            # correct because the right value happens to sit elsewhere in the
            # payload: a caller reading this field gets a wrong answer either
            # way, and crediting it here would let a schema violation score as
            # successful extraction. `available` still records that the value
            # was somewhere in the reply, which is what that column is for.
            detail[name] = {
                "state": "wrong",
                "correct": False,
                "available": in_whole,
                "value": value[:160],
            }

    required = task.get("output_schema", {}).get("required", [])
    schema_valid = bool(obj) and all(
        key in obj and obj[key] not in (None, "", [], {}) for key in required
    )
    return {
        "fields_scored": scored,
        "fields_correct": correct,
        "fields_available": available,
        "field_detail": detail,
        "schema_valid": schema_valid,
    }


# --------------------------------------------------------------------------
# Arms
# --------------------------------------------------------------------------


def run_exa_agent(
    task: Mapping[str, Any], cfg: Mapping[str, Any], timeout: float
) -> dict[str, Any]:
    key = _credential("exa-agent")
    headers = {"Authorization": f"Bearer {key}"}
    status, body = _post(
        "https://api.exa.ai/agent/runs",
        headers,
        {
            "query": task["prompt"],
            "effort": cfg["effort"],
            "outputSchema": task["output_schema"],
        },
        timeout,
    )
    if status != 200 or not isinstance(body, Mapping):
        return {"status": "failed", "error_class": f"create_{status}", "detail": str(body)[:300]}
    run_id = body.get("id")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(3)
        code, run = _get(
            f"https://api.exa.ai/agent/runs/{run_id}", headers, _poll_timeout(deadline)
        )
        if failed := _terminal_poll_error(code, run):
            return failed
        if not isinstance(run, Mapping):
            continue
        if run.get("status") in ("completed", "failed", "cancelled"):
            if run.get("status") != "completed":
                return {"status": "failed", "error_class": f"run_{run.get('status')}"}
            out = run.get("output") or {}
            grounding = out.get("grounding")
            cost = run.get("costDollars") or {}
            return {
                "status": "ok",
                "answer": out.get("structured"),
                "text": out.get("text"),
                "citation_count": len(grounding) if isinstance(grounding, list) else None,
                "cost_usd": cost.get("total"),
                "units": {"costDollars": dict(cost), "usage": run.get("usage")},
            }
    return {"status": "timeout", "error_class": "poll_timeout"}


def run_parallel_task(
    task: Mapping[str, Any], cfg: Mapping[str, Any], timeout: float
) -> dict[str, Any]:
    key = _credential("parallel-task")
    headers = {"x-api-key": key}
    status, body = _post(
        "https://api.parallel.ai/v1/tasks/runs",
        headers,
        {
            "input": task["prompt"],
            "task_spec": {
                "output_schema": {"type": "json", "json_schema": _closed(task["output_schema"])}
            },
            "processor": cfg["processor"],
        },
        timeout,
    )
    if status not in (200, 202) or not isinstance(body, Mapping):
        return {"status": "failed", "error_class": f"create_{status}", "detail": str(body)[:300]}
    run_id = body.get("run_id")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(3)
        code, run = _get(
            f"https://api.parallel.ai/v1/tasks/runs/{run_id}", headers, _poll_timeout(deadline)
        )
        if failed := _terminal_poll_error(code, run):
            return failed
        if not isinstance(run, Mapping):
            continue
        if run.get("status") in ("completed", "failed", "cancelled"):
            if run.get("status") != "completed":
                return {"status": "failed", "error_class": f"run_{run.get('status')}"}
            rcode, res = _get_parallel_result(run_id, headers, deadline)
            if not isinstance(res, Mapping):
                return {"status": "failed", "error_class": f"result_{rcode}"}
            out = res.get("output") or {}
            basis = out.get("basis") if isinstance(out, Mapping) else None
            content = out.get("content") if isinstance(out, Mapping) else None
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except ValueError:
                    pass
            return {
                "status": "ok",
                "answer": content,
                "text": None,
                "citation_count": len(basis) if isinstance(basis, list) else None,
                # Parallel does not return spend on the run; the list price for
                # the selected processor is recorded separately, never inferred
                # here as if it were measured.
                "cost_usd": None,
                "units": {"run_id": run_id, "processor": cfg["processor"]},
            }
    return {"status": "timeout", "error_class": "poll_timeout"}


def run_firecrawl_agent(
    task: Mapping[str, Any], cfg: Mapping[str, Any], timeout: float
) -> dict[str, Any]:
    key = _credential("firecrawl-agent")
    headers = {"Authorization": f"Bearer {key}"}
    status, body = _post(
        "https://api.firecrawl.dev/v2/agent",
        headers,
        {
            "prompt": task["prompt"],
            "schema": task["output_schema"],
            "model": "spark-2",
            "maxCredits": cfg["max_credits"],
        },
        timeout,
    )
    if status != 200 or not isinstance(body, Mapping):
        return {"status": "failed", "error_class": f"create_{status}", "detail": str(body)[:300]}
    run_id = body.get("id")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(4)
        code, run = _get(
            f"https://api.firecrawl.dev/v2/agent/{run_id}", headers, _poll_timeout(deadline)
        )
        if failed := _terminal_poll_error(code, run):
            return failed
        if not isinstance(run, Mapping):
            continue
        if run.get("status") in ("completed", "failed", "cancelled"):
            if run.get("status") != "completed":
                return {"status": "failed", "error_class": f"run_{run.get('status')}"}
            credits = run.get("creditsUsed")
            return {
                "status": "ok",
                "answer": run.get("data"),
                "text": run.get("message"),
                # Firecrawl's agent returns no citation/grounding field at all.
                # None means "not offered by the product", not "zero found".
                "citation_count": None,
                "cost_usd": (credits * CREDIT_USD) if isinstance(credits, (int, float)) else None,
                "units": {"creditsUsed": credits, "model": run.get("model")},
            }
    return {"status": "timeout", "error_class": "poll_timeout"}


def run_perplexity_agent(
    task: Mapping[str, Any], cfg: Mapping[str, Any], timeout: float
) -> dict[str, Any]:
    """Perplexity Agent API: one synchronous web-grounded structured answer.

    Request and response per docs.perplexity.ai/api-reference/agent-post,
    verified live 2026-09-25. ``POST /v1/agent`` takes a ``preset`` (which
    bundles model, step budget and web tools -- so ``tools`` is deliberately
    not sent, since passing it would replace the preset's set) and a
    ``response_format`` JSON schema. The wire response has no ``output_text``;
    that is an SDK convenience, so the text is assembled from the
    ``output_text`` parts of ``message`` items exactly as the SDK does. Spend
    is reported in ``usage.cost.total_cost`` (USD).
    """
    key = _credential("perplexity-agent")
    status, body = _post(
        "https://api.perplexity.ai/v1/agent",
        {"Authorization": f"Bearer {key}"},
        {
            "preset": cfg["preset"],
            "input": task["prompt"],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    # 1-64 chars; task ids carry hyphens.
                    "name": re.sub(r"[^A-Za-z0-9_]", "_", f"sew_{task['id']}")[:64],
                    "schema": _closed(task["output_schema"]),
                },
            },
        },
        timeout,
    )
    if status != 200 or not isinstance(body, Mapping):
        return {"status": "failed", "error_class": f"create_{status}", "detail": str(body)[:300]}
    if body.get("status") != "completed":
        return {"status": "failed", "error_class": f"run_{body.get('status')}"}
    return _perplexity_agent_result(body)


def _perplexity_agent_result(body: Mapping[str, Any]) -> dict[str, Any]:
    text_parts: list[str] = []
    cited_urls: set[str] = set()
    retrieved = 0
    for item in body.get("output") or []:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") == "search_results":
            retrieved += len(item.get("results") or [])
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if not isinstance(part, Mapping) or part.get("type") != "output_text":
                continue
            text_parts.append(str(part.get("text") or ""))
            for note in part.get("annotations") or []:
                if isinstance(note, Mapping) and note.get("url"):
                    cited_urls.add(str(note["url"]))
    text = "".join(text_parts)
    try:
        answer: Any = json.loads(text)
    except (TypeError, ValueError):
        # Unparseable structured output is a schema failure for the scorer,
        # not a crash: keep the prose so `available` can still be measured.
        answer = None
    usage = body.get("usage") if isinstance(body.get("usage"), Mapping) else {}
    cost = usage.get("cost") if isinstance(usage.get("cost"), Mapping) else {}
    return {
        "status": "ok",
        "answer": answer,
        "text": text,
        # URL annotations are citations; retrieved search results are not --
        # an agent that read fifteen pages and cited none has cited none. In
        # structured mode Perplexity returns no annotations (its docs say to
        # take links from search_results), so absence is "not reported", None.
        "citation_count": len(cited_urls) if cited_urls else None,
        "cost_usd": cost.get("total_cost"),
        "units": {
            "model": body.get("model"),
            "search_results_retrieved": retrieved,
            "usage": dict(usage),
        },
    }


def run_brave_answers(
    task: Mapping[str, Any], cfg: Mapping[str, Any], timeout: float
) -> dict[str, Any]:
    """Brave Answers: OpenAI-compatible, web-grounded chat completion.

    ``POST /res/v1/chat/completions`` with the Answers key, verified live
    2026-09-25. It has no structured-output parameter, so the schema goes in
    the prompt and the reply is parsed; a reply that will not parse is a
    schema failure the scorer records, with the text kept so ``available``
    is still measured. Blocking mode returns no citations (``enable_citations``
    requires streaming), so ``citation_count`` is None -- not offered, not zero.
    """
    key = _credential("brave-answers")
    prompt = (
        f"{task['prompt']}\n\nReturn ONLY a JSON object matching this JSON Schema, "
        f"with no prose or code fences:\n{json.dumps(task['output_schema'])}"
    )
    body: dict[str, Any] = {
        "model": "brave",
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    if cfg.get("search_context_size"):
        body["web_search_options"] = {"search_context_size": cfg["search_context_size"]}
    status, response = _post(
        "https://api.search.brave.com/res/v1/chat/completions",
        {"X-Subscription-Token": key},
        body,
        timeout,
    )
    if status != 200 or not isinstance(response, Mapping):
        return {
            "status": "failed",
            "error_class": f"create_{status}",
            "detail": str(response)[:300],
        }
    choices = response.get("choices") or [{}]
    message = choices[0].get("message") if isinstance(choices[0], Mapping) else {}
    text = str((message or {}).get("content") or "")
    usage = response.get("usage") if isinstance(response.get("usage"), Mapping) else {}
    tokens = usage.get("total_tokens")
    cost = (
        BRAVE_ANSWERS_USD_PER_REQUEST + tokens * BRAVE_ANSWERS_USD_PER_TOKEN
        if isinstance(tokens, (int, float))
        else None
    )
    return {
        "status": "ok",
        "answer": _parse_json_reply(text),
        "text": text,
        "citation_count": None,
        "cost_usd": cost,
        "units": {"model": response.get("model"), "usage": dict(usage)},
    }


def _parse_json_reply(text: str) -> Any:
    """A JSON object from a prompted reply, tolerating a markdown code fence."""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.split("\n", 1)[1] if "\n" in candidate else ""
        candidate = candidate.rsplit("```", 1)[0]
    try:
        return json.loads(candidate.strip())
    except (TypeError, ValueError):
        return None


def _tavily_output_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Tavily accepts only ``properties`` and ``required`` at the top level.

    Sending a standard JSON Schema is a 400 ("Output schema contains
    unexpected keys: type"), observed live 2026-09-25. Nested property
    definitions keep their `type`/`items`/`description`.
    """
    return {
        "properties": dict(schema.get("properties") or {}),
        "required": list(schema.get("required") or []),
    }


def run_tavily_research(
    task: Mapping[str, Any], cfg: Mapping[str, Any], timeout: float
) -> dict[str, Any]:
    """Tavily Research: submit, then poll for a structured, cited report.

    ``POST /research {input, model, output_schema}`` returns a ``request_id``;
    ``GET /research/{request_id}`` reports ``pending``/``processing`` until
    ``completed`` or ``failed``. Verified live 2026-09-25: with an output
    schema, ``content`` is the structured object and ``sources`` lists the
    cited pages. The response reports no spend.
    """
    deadline = time.monotonic() + timeout
    key = _credential("tavily-research")
    headers = {"Authorization": f"Bearer {key}"}
    status, body = _post(
        "https://api.tavily.com/research",
        headers,
        {
            "input": task["prompt"],
            "model": cfg["model"],
            "output_schema": _tavily_output_schema(task["output_schema"]),
        },
        timeout,
    )
    if status not in (200, 201, 202) or not isinstance(body, Mapping):
        return {"status": "failed", "error_class": f"create_{status}", "detail": str(body)[:300]}
    request_id = body.get("request_id")
    while (remaining := deadline - time.monotonic()) > 0:
        time.sleep(min(5.0, remaining))
        if time.monotonic() >= deadline:
            break
        code, run = _get(
            f"https://api.tavily.com/research/{request_id}", headers, _poll_timeout(deadline)
        )
        if failed := _terminal_poll_error(code, run):
            return failed
        if not isinstance(run, Mapping):
            continue
        if run.get("status") in ("completed", "failed"):
            if run.get("status") != "completed":
                return {"status": "failed", "error_class": f"run_{run.get('status')}"}
            content = run.get("content")
            sources = [s for s in run.get("sources") or [] if isinstance(s, Mapping)]
            cited = {str(s["url"]) for s in sources if s.get("url")}
            return {
                "status": "ok",
                "answer": content
                if isinstance(content, Mapping)
                else _parse_json_reply(str(content)),
                "text": content if isinstance(content, str) else json.dumps(content),
                # Tavily's `sources` are the report's citations by contract.
                "citation_count": len(cited),
                "cost_usd": None,
                "units": {"model": cfg["model"], "response_time": run.get("response_time")},
            }
    return {"status": "timeout", "error_class": "poll_timeout"}


def run_control_diy(
    task: Mapping[str, Any], cfg: Mapping[str, Any], timeout: float
) -> dict[str, Any]:
    """DIY floor: one search, and what it would cost to put in context.

    This is the denominator for every token claim in this lane. It is a lower
    bound by construction -- a real DIY attempt at a multi-field research task
    issues several searches and then pays for synthesis on top -- so a
    reduction measured against it understates the true saving.
    """
    provider = make_provider(cfg.get("provider", "parallel-web"), live_enabled=True)
    request = ProviderRequest(
        operation="search",
        run_id="control",
        query=task["prompt"],
        options={"num_results": 5, "max_chars_per_result": 4000, "mode": "fast"},
        timeout_seconds=timeout,
    )
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        result = provider.call(request)
        if result.status != "rate_limited" or attempt == RATE_LIMIT_RETRIES:
            break
        time.sleep(
            _rate_limit_delay(
                attempt,
                base_seconds=RATE_LIMIT_BACKOFF_SECONDS,
                retries=RATE_LIMIT_RETRIES,
            )
        )
    if result.status != "ok":
        return {"status": result.status, "error_class": result.error_class}
    chars = sum(int(s.get("content_length") or 0) for s in result.sources)
    corpus = "\n".join(f"{s.get('title', '')}\n{s.get('snippet', '')}" for s in result.sources)
    return {
        "status": "ok",
        # There is no structured answer: that is the point. The control shows
        # the raw material an agent would have to reason over itself.
        "answer": None,
        "text": corpus,
        "citation_count": len(result.sources),
        "cost_usd": (result.provider_cost or {}).get("total")
        if isinstance(result.provider_cost, Mapping)
        else None,
        "units": {"provider": provider.provider_id, "result_count": len(result.sources)},
        "context_chars": chars,
    }


ARM_RUNNERS = {
    "exa-agent": run_exa_agent,
    "parallel-task": run_parallel_task,
    "firecrawl-agent": run_firecrawl_agent,
    "perplexity-agent": run_perplexity_agent,
    "brave-answers": run_brave_answers,
    "tavily-research": run_tavily_research,
    CONTROL_ARM: run_control_diy,
}


def _closed(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Parallel requires additionalProperties:false on object schemas."""
    out = json.loads(json.dumps(schema))

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                node.setdefault("additionalProperties", False)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(out)
    return out


def load_tasks(path: Path | None = None) -> dict[str, Any]:
    import yaml

    target = path or module_root() / "catalogs" / "agent" / "tasks.yaml"
    if not target.exists():
        raise AgentLaneError(f"agent task catalog not found: {target}")
    data = yaml.safe_load(target.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise AgentLaneError(f"malformed agent task catalog: {target}")
    return data


def run_arm(
    arm: str, task: Mapping[str, Any], tier: str, repetition: int, timeout: float
) -> ArmResult:
    cfg = TIERS[tier].get(arm, {}) if arm != CONTROL_ARM else {}
    started = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    t0 = time.perf_counter()
    try:
        out = ARM_RUNNERS[arm](task, cfg, timeout)
    except Exception as exc:  # noqa: BLE001
        out = {"status": "failed", "error_class": f"runner_exception:{type(exc).__name__}: {exc}"}
    latency_ms = (time.perf_counter() - t0) * 1000.0

    if out.get("status") != "ok":
        return ArmResult(
            task_id=task["id"],
            task_class=task["task_class"],
            arm=arm,
            tier=tier,
            repetition=repetition,
            status=str(out.get("status")),
            latency_ms=latency_ms,
            error_class=str(out.get("error_class")),
            started_at=started,
        )

    answer = out.get("answer")
    # The control arm has no structured answer by design; score its raw corpus
    # for answer-presence so the two arms are compared on the same question:
    # "is the verified value available at this point?"
    scored = score_answer(task, answer if answer is not None else out.get("text"))
    payload = out.get("context_chars")
    chars = (
        int(payload) if payload is not None else len(_stringify(answer) + (out.get("text") or ""))
    )
    return ArmResult(
        task_id=task["id"],
        task_class=task["task_class"],
        arm=arm,
        tier=tier,
        repetition=repetition,
        status="ok",
        latency_ms=latency_ms,
        citation_count=out.get("citation_count"),
        vendor_cost_usd=out.get("cost_usd"),
        vendor_units=dict(out.get("units") or {}),
        answer_chars=chars,
        answer_tokens_est=int(chars / CHARS_PER_TOKEN),
        raw_answer=answer if answer is not None else (out.get("text") or "")[:400],
        started_at=started,
        **scored,
    )


def aggregate(cells: Sequence[ArmResult], catalog: Mapping[str, Any]) -> dict[str, Any]:
    by_task = {t["id"]: t for t in catalog["tasks"]}
    arms = sorted({c.arm for c in cells})

    # A field every vendor arm gets wrong is likelier bad ground truth than a
    # simultaneous three-way failure; flag it rather than book three misses.
    suspect: list[str] = []
    vendor = [c for c in cells if c.arm in AGENT_ARMS and c.status == "ok"]
    for tid, task in by_task.items():
        for spec in task.get("fields", []):
            name = spec["name"]
            # Repetitions are not independent provider votes. Group scorable
            # observations by vendor arm first so one noisy arm cannot create
            # apparent multi-provider agreement. Missing and optional fields
            # (`correct is None`) likewise provide no vote about ground truth.
            observations_by_arm: dict[str, list[bool]] = {}
            for cell in vendor:
                if cell.task_id != tid:
                    continue
                correct = cell.field_detail.get(name, {}).get("correct")
                if correct is not None:
                    observations_by_arm.setdefault(cell.arm, []).append(bool(correct))
            if len(observations_by_arm) >= 2 and all(
                not any(observations) for observations in observations_by_arm.values()
            ):
                suspect.append(f"{tid}.{name}")

    def summarize(subset: Sequence[ArmResult]) -> dict[str, Any]:
        ok = [c for c in subset if c.status == "ok"]
        lat = [c.latency_ms for c in ok]
        scored = sum(
            sum(
                1
                for k in c.field_detail
                if f"{c.task_id}.{k}" not in suspect
                and c.field_detail[k].get("correct") is not None
            )
            for c in ok
        )
        correct = sum(
            sum(
                1
                for k, v in c.field_detail.items()
                if f"{c.task_id}.{k}" not in suspect and v.get("correct") is True
            )
            for c in ok
        )
        avail = sum(
            sum(
                1
                for k, v in c.field_detail.items()
                if f"{c.task_id}.{k}" not in suspect and v.get("available") is True
            )
            for c in ok
        )
        toks = [c.answer_tokens_est for c in ok]
        costs = [c.vendor_cost_usd for c in ok if c.vendor_cost_usd is not None]
        cites = [c.citation_count for c in ok if c.citation_count is not None]
        complete = [c for c in ok if c.schema_valid]
        lo, hi = wilson(correct, scored)
        return {
            "n": len(subset),
            "ok": len(ok),
            "errors": len(subset) - len(ok),
            "field_accuracy": round(correct / scored, 4) if scored else None,
            "field_accuracy_ci95": [round(lo, 4), round(hi, 4)] if scored else None,
            "fields_scored": scored,
            "fields_correct": correct,
            "fields_available": avail,
            "answer_available_rate": round(avail / scored, 4) if scored else None,
            "schema_complete_rate": round(len(complete) / len(ok), 4) if ok else None,
            "latency_p50_ms": round(pct(lat, 0.50), 1) if lat else None,
            "latency_p95_ms": round(pct(lat, 0.95), 1) if lat else None,
            "tokens_est_p50": int(pct([float(t) for t in toks], 0.50)) if toks else None,
            "tokens_est_mean": int(statistics.fmean(toks)) if toks else None,
            "vendor_cost_mean_usd": round(statistics.fmean(costs), 6) if costs else None,
            "vendor_cost_total_usd": round(sum(costs), 6) if costs else None,
            "citations_mean": round(statistics.fmean(cites), 2) if cites else None,
        }

    per_arm = {a: summarize([c for c in cells if c.arm == a]) for a in arms}

    # The headline of this lane: tokens spent per correct field, vendor vs DIY.
    control = per_arm.get(CONTROL_ARM, {})
    ctl_tokens = control.get("tokens_est_p50")
    for arm, summary in per_arm.items():
        tokens = summary.get("tokens_est_p50")
        if arm != CONTROL_ARM and ctl_tokens and tokens:
            summary["token_reduction_vs_control"] = round(1 - (tokens / ctl_tokens), 4)
            summary["control_tokens_per_vendor_token"] = round(ctl_tokens / tokens, 1)
    return {
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "catalog_id": catalog.get("catalog_id"),
        "total_cells": len(cells),
        "suspect_ground_truth": sorted(set(suspect)),
        "by_arm": per_arm,
        "by_task": {
            tid: {
                a: {
                    "accuracy": [
                        f"{c.fields_correct}/{c.fields_scored}"
                        for c in cells
                        if c.task_id == tid and c.arm == a
                    ],
                    "status": sorted({c.status for c in cells if c.task_id == tid and c.arm == a}),
                    "latency_p50_ms": round(
                        pct([c.latency_ms for c in cells if c.task_id == tid and c.arm == a], 0.5),
                        1,
                    ),
                    "tokens_est": [
                        c.answer_tokens_est for c in cells if c.task_id == tid and c.arm == a
                    ],
                }
                for a in arms
            }
            for tid in by_task
            if any(c.task_id == tid for c in cells)
        },
    }


def run_lane(
    output_root: Path,
    *,
    tier: str = "mid",
    repetitions: int = 2,
    arms: Sequence[str] = AGENT_ARMS + (CONTROL_ARM,),
    task_ids: Iterable[str] | None = None,
    timeout_seconds: float = 600.0,
    catalog_path: Path | None = None,
    max_cells: int | None = None,
    progress: bool = True,
) -> dict[str, Any]:
    if tier not in TIERS:
        raise AgentLaneError(f"unknown tier {tier!r}; expected one of {sorted(TIERS)}")
    catalog = load_tasks(catalog_path)
    tasks = catalog["tasks"]
    if task_ids is not None:
        wanted = set(task_ids)
        tasks = [t for t in tasks if t["id"] in wanted]
    if not tasks:
        raise AgentLaneError("no tasks selected")

    run_id = f"agent-{tier}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    plan = [(rep, t, a) for rep in range(1, repetitions + 1) for t in tasks for a in arms]
    if max_cells is not None:
        plan = plan[:max_cells]
    total = len(plan)

    # Arms run concurrently; each arm's own cells run serially. Parallelising
    # WITHIN an arm would pile concurrent requests onto one vendor and turn the
    # experiment into a rate-limit test -- Firecrawl already 429s under the L1
    # lane's serial pace. Across arms there is no shared limit, and wall time
    # collapses to the slowest arm (Parallel `pro` can take 10 minutes a run).
    by_arm: dict[str, list[tuple[int, Mapping[str, Any]]]] = {a: [] for a in arms}
    for rep, task, arm in plan:
        by_arm[arm].append((rep, task))

    cells: list[ArmResult] = []
    lock = threading.Lock()
    handle = (run_dir / "cells.jsonl").open("w", encoding="utf-8")
    done = 0

    def drive(arm: str) -> None:
        nonlocal done
        for rep, task in by_arm[arm]:
            cell = run_arm(arm, task, tier, rep, timeout_seconds)
            with lock:
                cells.append(cell)
                handle.write(json.dumps(asdict(cell), sort_keys=True, default=str) + "\n")
                handle.flush()
                done += 1
                if progress:
                    mark = "ok " if cell.status == "ok" else "ERR"
                    acc = (
                        f"{cell.fields_correct}/{cell.fields_scored}"
                        if cell.status == "ok"
                        else "-"
                    )
                    print(
                        f"[{done:>3}/{total}] {mark} {arm:<16} {task['id']:<24} rep{rep} "
                        f"{cell.latency_ms / 1000:>6.1f}s acc={acc:<5} tok={cell.answer_tokens_est}",
                        flush=True,
                    )

    try:
        with ThreadPoolExecutor(max_workers=max(1, len(arms))) as pool:
            list(pool.map(drive, [a for a in arms if by_arm[a]]))
    finally:
        handle.close()

    report = aggregate(cells, catalog)
    report.update(
        {
            "run_id": run_id,
            "tier": tier,
            "repetitions": repetitions,
            "arms": list(arms),
            "tier_config": TIERS[tier],
        }
    )
    (run_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
    (run_dir / "report.md").write_text(render_markdown(report))
    report["run_dir"] = str(run_dir)
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        f"# SEW Agent Lane: {report['catalog_id']} / tier={report['tier']}",
        "",
        f"- Run: `{report['run_id']}`   Generated: {report['generated_at']}",
        f"- Repetitions: {report['repetitions']}   Cells: {report['total_cells']}",
        f"- Tier config: `{json.dumps(report['tier_config'], sort_keys=True)}`",
    ]
    if report["suspect_ground_truth"]:
        lines.append(
            f"- **Excluded as suspect ground truth**: {', '.join(report['suspect_ground_truth'])}"
        )
    lines += [
        "",
        "## Headline",
        "",
        "| arm | n | err | extracted correct (95% CI) | answer available | complete | "
        "p50 | tokens est | vs DIY | citations | cost |",
        "| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for arm, s in report["by_arm"].items():
        acc = (
            f"{s['field_accuracy'] * 100:.0f}% "
            f"({s['field_accuracy_ci95'][0] * 100:.0f}-{s['field_accuracy_ci95'][1] * 100:.0f})"
            if s["field_accuracy"] is not None
            else "n/a"
        )
        red = (
            f"{s['control_tokens_per_vendor_token']}x less"
            if s.get("control_tokens_per_vendor_token")
            else "baseline"
        )
        cost = (
            f"${s['vendor_cost_mean_usd']:.4f}"
            if s["vendor_cost_mean_usd"] is not None
            else "unreported"
        )
        comp = (
            f"{s['schema_complete_rate'] * 100:.0f}%"
            if s["schema_complete_rate"] is not None
            else "n/a"
        )
        lat = f"{s['latency_p50_ms'] / 1000:.1f}s" if s["latency_p50_ms"] else "n/a"
        cites = s["citations_mean"] if s["citations_mean"] is not None else "none"
        avail = (
            f"{s['answer_available_rate'] * 100:.0f}%"
            if s.get("answer_available_rate") is not None
            else "n/a"
        )
        lines.append(
            f"| {arm} | {s['n']} | {s['errors']} | {acc} | {avail} | {comp} | {lat} | "
            f"{s['tokens_est_p50'] or 0:,} | {red} | {cites} | {cost} |"
        )
    lines += [
        "",
        "## Per task",
        "",
        "| task | " + " | ".join(report["arms"]) + " |",
        "| --- | " + " | ".join("---" for _ in report["arms"]) + " |",
    ]
    for tid, per in report["by_task"].items():
        cells = []
        for a in report["arms"]:
            d = per.get(a, {})
            cells.append(
                f"{','.join(d.get('accuracy', []) or ['-'])} / {d.get('latency_p50_ms', 0) / 1000:.0f}s"
            )
        lines.append(f"| {tid} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
