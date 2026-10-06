"""MCP spend evidence, generalized from SEW's vendor meter.

Tariffs are local pricing evidence; unknown and changed vendor modes remain unpriced.
"""

from __future__ import annotations
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any


def response_documents(result: Any) -> list[Any]:
    """The JSON documents inside an MCP ``tools/call`` result, best effort."""

    documents: list[Any] = []
    if not isinstance(result, Mapping):
        return documents
    structured = result.get("structuredContent")
    if isinstance(structured, (Mapping, list)):
        documents.append(structured)
    for item in result.get("content") or []:
        if isinstance(item, Mapping) and item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str) and text.lstrip()[:1] in ("{", "["):
                try:
                    documents.append(json.loads(text))
                except ValueError:
                    continue
    return documents


def measured_spend(
    documents: Sequence[Any],
    *,
    server: str | None = None,
    arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Spend the vendor reported in its own response.

    A `creditsCost` receipt is only trusted on a Firecrawl call that routed
    through a billed `alexandria` capability; elsewhere the same field is just
    data in a result and must not override the tariff.
    """

    receipt_call = server == "firecrawl" and (arguments or {}).get("alexandria") not in (
        None,
        "",
        [],
        {},
    )

    for document in documents:
        if not isinstance(document, Mapping):
            continue
        cost_dollars = document.get("costDollars")
        if isinstance(cost_dollars, Mapping) and _number(cost_dollars.get("total")) is not None:
            return {"provider_cost": {"currency": "USD", "total": _number(cost_dollars["total"])}}
        usage = document.get("usage")
        if isinstance(usage, Mapping):
            cost = usage.get("cost")
            total = _number(cost.get("total_cost")) if isinstance(cost, Mapping) else None
            if total is not None:
                return {"provider_cost": {"currency": "USD", "total": total}}
            credits = _number(usage.get("credits"))
            if credits is not None:
                return {"provider_units": {"credits_used": credits, "credits_source": "response"}}
        # Firecrawl reports `creditsUsed`; a billed `alexandria` capability
        # returns a `creditsCost` receipt. Either is the vendor's own count.
        for key in ("creditsUsed", "creditsCost") if receipt_call else ("creditsUsed",):
            credits = _number(document.get(key))
            if credits is not None:
                return {"provider_units": {"credits_used": credits, "credits_source": "response"}}
        data = document.get("data")
        if receipt_call and isinstance(data, Mapping):
            # Inline Alexandria responses keep the summed receipt in data;
            # retained large responses also promote it to the top level.
            credits = _number(data.get("creditsCost"))
            if credits is not None:
                return {"provider_units": {"credits_used": credits, "credits_source": "response"}}
    return {}


def tariff_spend(
    rule: Mapping[str, Any] | None, arguments: Mapping[str, Any], documents: Sequence[Any]
) -> dict[str, Any]:
    """Spend a published tariff assigns to one call, or why it cannot."""

    if not rule:
        return {"unpriced_reason": "no_tariff_for_tool"}
    if rule.get("unpriced_reason"):
        return {"unpriced_reason": rule["unpriced_reason"]}
    for name in rule.get("unpriced_if_args") or []:
        if arguments.get(name) not in (None, "", [], {}):
            return {"unpriced_reason": f"argument_changes_price:{name}"}
    unrecognized = _unrecognized_argument(rule, arguments)
    if unrecognized:
        return {"unpriced_reason": f"unrecognized_argument:{unrecognized}"}
    if "pricing_tier" in rule:
        tier = _by_argument(rule["pricing_tier"], arguments)
        if tier is None:
            return {"unpriced_reason": rule.get("missing_tier_reason", "pricing_tier_not_observed")}
        spend = {"pricing_tier": tier}
        units = {}
        if rule.get("request_count_arg"):
            count = result_count(documents)
            source = "response"
            if count is None:
                urls = arguments.get(rule["request_count_arg"])
                if (
                    not isinstance(urls, list)
                    or not urls
                    or not all(isinstance(u, str) and u for u in urls)
                ):
                    return {"unpriced_reason": "page_count_unknown"}
                count, source = len(urls), "estimated_from_request"
            units.update(requests=count, requests_source=source)
        if "included_results" in rule:
            limit = arguments.get(rule["result_limit_arg"], rule["default_result_limit"])
            if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
                return {"unpriced_reason": "invalid_result_limit"}
            count = result_count(documents)
            if count is None and limit > rule["included_results"]:
                return {"unpriced_reason": "additional_result_count_unknown"}
            units["additional_results"] = max(0, (count or 0) - rule["included_results"])
        if units:
            spend["provider_units"] = units
        return spend
    credits = _by_argument(rule.get("credits"), arguments)
    block = rule.get("credits_per_block")
    if isinstance(block, Mapping):
        count = result_count(documents)
        estimated = False
        if count is None:
            # A text-only response hides its result count; a rule may price
            # the requested URL list instead, marked as an estimate.
            request_arg = rule.get("request_count_arg")
            requested = arguments.get(request_arg) if isinstance(request_arg, str) else None
            if (
                not isinstance(requested, list)
                or not requested
                or not all(isinstance(url, str) and url for url in requested)
            ):
                return {"unpriced_reason": "result_count_unknown"}
            count = len(requested)
            estimated = True
        per_block = _by_argument(block.get("credits"), arguments)
        size = _number(block.get("size"))
        if per_block is None or not size:
            return {"unpriced_reason": "tariff_incomplete"}
        credits = (credits or 0) + per_block * math.ceil(count / size)
    extra = rule.get("extra_credits_if_arg_contains")
    if isinstance(extra, Mapping) and credits is not None:
        if _argument_contains(arguments.get(extra.get("arg")), extra.get("values") or []):
            credits += _number(extra.get("credits")) or 0
    if credits is None:
        return {"unpriced_reason": "tariff_incomplete"}
    units = {"credits_used": credits, "credits_source": "tariff"}
    if isinstance(block, Mapping) and estimated:
        units.update({"credits_source": "estimated_from_request", "requested_url_count": count})
    return {"provider_units": units}


def result_count(documents: Sequence[Any]) -> int | None:
    """How many results a response returned (search results, extracted URLs)."""

    for document in documents:
        if isinstance(document, list):
            return len(document)
        if not isinstance(document, Mapping):
            continue
        for key in ("results", "data", "web"):
            value = document.get(key)
            if isinstance(value, list):
                return len(value)
            if isinstance(value, Mapping) and isinstance(value.get("web"), list):
                return len(value["web"])
    return None


def _argument_specs(rule: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Every ``{arg, values, default}`` selector a rule prices by."""

    specs: list[Mapping[str, Any]] = []
    block = rule.get("credits_per_block")
    for spec in (
        rule.get("pricing_tier"),
        rule.get("credits"),
        block.get("credits") if isinstance(block, Mapping) else None,
    ):
        if isinstance(spec, Mapping) and spec.get("arg"):
            specs.append(spec)
    return specs


def _unrecognized_argument(rule: Mapping[str, Any], arguments: Mapping[str, Any]) -> str | None:
    """Selector name when its value is absent from the tariff's enum.

    An absent argument takes the published default; a present one the table
    does not know (a new vendor mode) is left unpriced rather than guessed.
    """

    for spec in _argument_specs(rule):
        name = str(spec["arg"])
        if name not in arguments or arguments[name] in (None, ""):
            continue
        value = arguments[name]
        if not isinstance(value, str) or value not in (spec.get("values") or {}):
            return name
    return None


def _by_argument(spec: Any, arguments: Mapping[str, Any]) -> Any:
    """A literal, or ``{arg, values, default}`` resolved against the call's arguments."""

    if not isinstance(spec, Mapping):
        return spec
    value = arguments.get(spec.get("arg"))
    values = spec.get("values") or {}
    if isinstance(value, str) and value in values:
        return values[value]
    return spec.get("default")


def _argument_contains(value: Any, wanted: Sequence[str]) -> bool:
    items = value if isinstance(value, list) else [value]
    for item in items:
        name = item.get("type") if isinstance(item, Mapping) else item
        if isinstance(name, str) and name in wanted:
            return True
    return False


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return value
