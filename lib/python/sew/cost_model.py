"""WSB-04 cost model: normalized spend and dollars per successful task.

Spend arrives on two meters that share nothing. Vendors bill per call: Exa
returns dollars on the response, Firecrawl and Tavily return credits whose
dollar value depends on the account's plan, and Parallel returns nothing and
publishes a list price per request. The harness bills per model token. This
module turns both into USD cost records, and every figure says how it was
obtained.

Three rules carry the whole module.

**Every figure carries its basis.** ``measured`` means the provider's own
response stated the dollars. ``inferred`` means a quantity was multiplied by a
rate from ``config/price-table.yaml``, and the component carries that rate,
its source URL, and the date it was read. ``unknown`` means neither was
possible; it carries a reason and an amount of ``None``.

**Unknown is never zero.** A cell with any unknown component has an unknown
total, and a cell with an unknown total is left out of every cost aggregate and
counted in a visible exclusion instead. An arm with no costed cell at all does
not appear in the cost table.

**Cost per success can be undefined.** It is total spend over successful
tasks, the only per-task number that penalizes a cheap arm that fails often.
With zero successes it is undefined (``None``), never infinity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .catalog import module_root

PRICE_TABLE_RELATIVE_PATH = Path("config") / "price-table.yaml"
BASES = ("measured", "inferred", "estimated", "unknown")
# operator_proxy_rate: no published per-call price exists, so the operator chose
# another vendor's published rate as a stand-in; the entry names it (proxy_for).
RATE_BASES = frozenset({"published_list_price", "plan_rate_assumption", "operator_proxy_rate", "list", "self-hosted"})
TOKENS_PER_MTOK = 1_000_000
# A call in these states never reached the vendor's meter.
UNBILLED_CALL_STATUSES = frozenset({"not_applicable"})
# Anthropic states the 1M context variant of Opus 5.5 uses standard rates.
STANDARD_RATE_VARIANTS = {"claude-opus-5-5[1m]": "claude-opus-5-5"}


class PriceTableError(ValueError):
    """Raised when the price table is malformed or a rate lacks its receipt."""


@dataclass(frozen=True)
class PriceTable:
    vendors: Mapping[str, Mapping[str, Any]]
    models: Mapping[str, Mapping[str, Any]]


def default_price_table_path() -> Path:
    return module_root() / PRICE_TABLE_RELATIVE_PATH


def load_price_table(path: Path | None = None) -> PriceTable:
    target = default_price_table_path() if path is None else path
    document = yaml.safe_load(target.read_text(encoding="utf-8"))
    return parse_price_table(document)


def parse_price_table(document: Any) -> PriceTable:
    if not isinstance(document, Mapping) or document.get("version") != 1:
        raise PriceTableError("price table must be a mapping with version: 1")
    vendors = _entries(document, "vendors")
    models = _entries(document, "models")
    for provider_id, entry in vendors.items():
        where = f"vendors.{provider_id}"
        _require_receipt(entry, where)
        meter = entry.get("meter")
        if meter == "credits":
            _require_rate(entry, "usd_per_credit", where)
        elif meter == "requests":
            tiers = entry.get("usd_per_request")
            if not isinstance(tiers, Mapping) or not tiers:
                raise PriceTableError(f"{where}.usd_per_request must be a non-empty mapping")
            for tier in tiers:
                _require_rate(tiers, tier, f"{where}.usd_per_request")
            if "usd_per_additional_result" in entry:
                _require_rate(entry, "usd_per_additional_result", where)
        else:
            raise PriceTableError(f"{where}.meter must be 'credits' or 'requests'")
    for provider_id, entry in vendors.items():
        if entry.get("rate_basis") == "operator_proxy_rate":
            target = entry["proxy_for"]
            if target not in vendors or target == provider_id:
                raise PriceTableError(f"vendors.{provider_id}.proxy_for must name another vendor")
    for model_id, entry in models.items():
        where = f"models.{model_id}"
        _require_receipt(entry, where)
        _require_rate(entry, "input_usd_per_mtok", where)
        _require_rate(entry, "output_usd_per_mtok", where)
        if entry.get("cached_input_usd_per_mtok") is not None:
            _require_rate(entry, "cached_input_usd_per_mtok", where)
        for bucket in (
            "cache_write_5m_usd_per_mtok",
            "cache_write_1h_usd_per_mtok",
            "cache_write_usd_per_mtok",
        ):
            if entry.get(bucket) is not None:
                _require_rate(entry, bucket, where)
        if entry.get("cache_write_usd_per_mtok") is not None and (
            entry.get("cache_write_5m_usd_per_mtok") is not None
            or entry.get("cache_write_1h_usd_per_mtok") is not None
        ):
            # One vendor publishes one write rate, another TTL-specific ones.
            # Both on one model would price TTL-less writes silently as exact.
            raise PriceTableError(
                f"{where} declares both cache_write_usd_per_mtok and TTL-specific write rates"
            )
        short_limit = entry.get("max_short_context_input_tokens")
        if short_limit is not None and (type(short_limit) is not int or short_limit <= 0):
            raise PriceTableError(
                f"{where}.max_short_context_input_tokens must be a positive integer"
            )
    return PriceTable(vendors=vendors, models=models)


def vendor_call(
    *,
    provider_id: str,
    status: str,
    ended_at: str | None,
    operation: str | None = None,
    provider_cost: Mapping[str, Any] | None = None,
    provider_units: Mapping[str, Any] | None = None,
    pricing_tier: str | None = None,
) -> dict[str, Any]:
    """Build the vendor-call input ``vendor_call_cost`` prices.

    ``pricing_tier`` names the list-price row for request-metered vendors
    (``search:fast``, ``task:core``); the caller knows which mode it asked for,
    and the price table must not guess it.
    """

    return {
        "provider_id": provider_id,
        "operation": operation,
        "status": status,
        "ended_at": ended_at,
        "provider_cost": dict(provider_cost) if provider_cost else None,
        "provider_units": dict(provider_units) if provider_units else None,
        "pricing_tier": pricing_tier,
    }


def vendor_call_from_record(record: Mapping[str, Any], *, provider_id: str) -> dict[str, Any]:
    """One adapter shared by run costs and vendor pricing coverage."""
    from .providers import provider_result_cost_metadata

    response = record.get("response")
    response = response if isinstance(response, Mapping) else {}
    metadata = provider_result_cost_metadata(response)
    tier = response.get("pricing_tier")
    meter = response.get("meter")
    meter = meter if isinstance(meter, Mapping) else {}
    billed_empty_result = (
        record.get("provider_id") == "brave"
        and record.get("operation") == "brave_web_search"
        and record.get("status") == "failed"
        and response.get("error_kind") == "empty_result"
        and meter.get("basis") == "billed_empty_result"
    )
    return vendor_call(
        provider_id=str(record.get("provider_id") or provider_id),
        status="ok" if billed_empty_result else str(record.get("status") or "failed"),
        ended_at=record.get("ended_at") if isinstance(record.get("ended_at"), str) else None,
        operation=record.get("operation"),
        provider_cost=metadata.get("provider_cost"),
        provider_units=metadata.get("provider_units"),
        pricing_tier=tier if isinstance(tier, str) else None,
    )


def vendor_call_from_result(result: Any, *, pricing_tier: str | None = None) -> dict[str, Any]:
    """Adapt a ``providers.ProviderResult`` to a vendor-call input."""

    return vendor_call(
        provider_id=result.provider_id,
        operation=result.operation,
        status=result.status,
        ended_at=result.ended_at,
        provider_cost=result.provider_cost,
        provider_units=result.provider_units,
        pricing_tier=pricing_tier,
    )


def vendor_call_cost(call: Mapping[str, Any], table: PriceTable) -> dict[str, Any]:
    """Price one vendor call: reported dollars, else credits or list price, else unknown."""

    provider_id = str(call.get("provider_id"))
    ended_at = call.get("ended_at")
    reported = _reported_usd(call.get("provider_cost"))
    if reported is not None:
        if not isinstance(ended_at, str) or not ended_at:
            return _unknown("vendor", provider_id, "reported_spend_without_timestamp")
        return _component(
            "vendor",
            provider_id,
            amount=reported,
            basis="measured",
            price_source="provider_response",
            price_as_of=ended_at,
            quantity={"reported_usd": reported},
        )

    entry = table.vendors.get(provider_id)
    units = call.get("provider_units") or {}
    credits = _credits_used(units)
    if credits is not None:
        if entry is None or entry.get("meter") != "credits":
            return _unknown("vendor", provider_id, "no_credit_rate")
        rate = float(entry["usd_per_credit"])
        return _component(
            "vendor",
            provider_id,
            amount=credits * rate,
            basis="estimated"
            if units.get("credits_source") == "estimated_from_request"
            else "inferred",
            price_source=entry["source"],
            price_as_of=entry["as_of"],
            quantity={
                "credits": credits,
                **(
                    {"requested_url_count": units["requested_url_count"]}
                    if units.get("credits_source") == "estimated_from_request"
                    and "requested_url_count" in units
                    else {}
                ),
            },
            assumed_rate={
                "usd_per_credit": rate,
                "rate_basis": entry.get("rate_basis"),
                "plan": entry.get("plan"),
            },
        )

    status = call.get("status")
    if status in UNBILLED_CALL_STATUSES:
        return _component(
            "vendor",
            provider_id,
            amount=0.0,
            basis="measured",
            price_source="no_request_issued",
            price_as_of=ended_at if isinstance(ended_at, str) else None,
            quantity={"requests": 0},
        )
    if status != "ok":
        # A failed call reported no spend; whether the vendor billed it is not
        # something the price table can know.
        return _unknown("vendor", provider_id, f"unreported_spend_on_{status}")
    if entry is None:
        return _unknown("vendor", provider_id, "no_reported_spend_and_no_price")
    if entry.get("meter") != "requests":
        return _unknown("vendor", provider_id, "credit_meter_without_credits_reported")
    tier = call.get("pricing_tier")
    tiers = entry["usd_per_request"]
    if tier not in tiers:
        return _unknown("vendor", provider_id, f"no_list_price_for_tier:{tier}")
    rate = float(tiers[tier])
    requests = units.get("requests", 1)
    additional = units.get("additional_results", 0)
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in (requests, additional)):
        return _unknown("vendor", provider_id, "invalid_request_quantity")
    extra_rate = entry.get("usd_per_additional_result")
    if additional and extra_rate is None:
        return _unknown("vendor", provider_id, "no_additional_result_rate")
    component = _component(
        "vendor",
        provider_id,
        amount=rate * requests + additional * float(extra_rate or 0),
        basis="estimated"
        if units.get("requests_source") == "estimated_from_request"
        else "inferred",
        price_source=entry["source"],
        price_as_of=entry["as_of"],
        quantity={"requests": requests, "pricing_tier": tier, "additional_results": additional},
        assumed_rate={
            "usd_per_request": rate,
            **({"usd_per_additional_result": extra_rate} if additional else {}),
            "rate_basis": entry.get("rate_basis"),
            **(
                {"proxy_for": entry["proxy_for"]}
                if entry.get("rate_basis") == "operator_proxy_rate"
                else {}
            ),
        },
    )
    if entry.get("rate_basis") == "operator_proxy_rate":
        component["pricing_note"] = f"proxy_rate:{provider_id}->{entry['proxy_for']}"
    return component


def model_cost(
    model_id: str | None, token_usage: Mapping[str, Any] | None, table: PriceTable
) -> dict[str, Any]:
    """Price harness model tokens at the model's published rates.

    ``token_usage`` is a WSB-03 ``account_tokens`` record: ``input`` is already
    net of ``cached_input``, and reasoning tokens are part of ``output`` on
    every harness that reports them, so they are never priced a second time.
    """

    subject = model_id or "unknown-model"
    if not model_id:
        return _unknown("model", subject, "model_id_unknown")
    usage = token_usage or {}
    if usage.get("accounting_source", "unknown") == "unknown":
        return _unknown("model", subject, "token_usage_unknown")
    input_tokens = usage.get("input")
    output_tokens = usage.get("output")
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        return _unknown("model", subject, "token_usage_incomplete")
    if model_id.startswith("litellm/"):
        from .oss import load_catalog

        entry = load_catalog().get(model_id[len("litellm/"):])
    else:
        entry = table.models.get(STANDARD_RATE_VARIANTS.get(model_id, model_id))
    if entry is None:
        return _unknown("model", subject, "no_published_rate")
    cached_tokens = usage.get("cached_input") or 0
    short_limit = entry.get("max_short_context_input_tokens")
    if short_limit is not None and input_tokens + cached_tokens > short_limit:
        # OpenAI reprices the *full request* above the threshold. A Codex usage
        # block sums every request in a turn, so only per-request usage could
        # show whether any single request crossed it.
        return _unknown("model", subject, "long_context_rate_requires_per_request_usage")
    cached_rate = entry.get("cached_input_usd_per_mtok")
    if cached_tokens and cached_rate is None:
        return _unknown("model", subject, "no_published_cached_input_rate")
    input_rate = float(entry["input_usd_per_mtok"])
    output_rate = float(entry["output_usd_per_mtok"])
    # OpenAI publishes one cache-write rate; Anthropic has TTL-specific rates.
    single_write_rate = entry.get("cache_write_usd_per_mtok")
    write_5m_rate = entry.get("cache_write_5m_usd_per_mtok", single_write_rate)
    write_1h_rate = entry.get("cache_write_1h_usd_per_mtok", single_write_rate)
    declared_write_rates = write_5m_rate is not None and write_1h_rate is not None
    writes = usage.get("cache_write")
    write_5m = usage.get("cache_write_5m", 0)
    write_1h = usage.get("cache_write_1h", 0)
    pricing_note = None
    if writes is None:
        if write_5m or write_1h:
            return _unknown("model", subject, "invalid_cache_write_usage")
        # Older bundles merged writes into input. Preserve their lower-bound
        # number, but do not call it a published-rate calculation.
        writes = 0
        if subject.startswith("claude-") or write_5m_rate is not None or write_1h_rate is not None:
            pricing_note = "cache_writes_priced_at_base_input"
    elif (
        not isinstance(writes, int)
        or isinstance(writes, bool)
        or writes < 0
        or writes > input_tokens
        or not isinstance(write_5m, int)
        or isinstance(write_5m, bool)
        or not isinstance(write_1h, int)
        or isinstance(write_1h, bool)
        or write_5m < 0
        or write_1h < 0
        or write_5m + write_1h > writes
    ):
        return _unknown("model", subject, "invalid_cache_write_usage")
    if writes and not declared_write_rates:
        # Other Claude rows have no write-rate receipt yet. Retain their
        # former base-rate lower bound with an honest estimated label.
        pricing_note = "cache_write_rate_missing_priced_at_base_input"
        write_5m_rate = input_rate
        write_1h_rate = input_rate
    unspecified_writes = writes - write_5m - write_1h
    if unspecified_writes and pricing_note is None and single_write_rate is None:
        # Five-minute writes are the cheaper declared rate. The missing TTL
        # breakdown makes this a visible lower-bound estimate.
        pricing_note = "cache_write_ttl_unknown_priced_at_5m_rate"
    amount = (
        (input_tokens - writes) * input_rate
        + (write_5m + unspecified_writes) * float(write_5m_rate or 0.0)
        + write_1h * float(write_1h_rate or 0.0)
        + cached_tokens * float(cached_rate or 0.0)
        + output_tokens * output_rate
    ) / TOKENS_PER_MTOK
    component = _component(
        "model",
        subject,
        amount=amount,
        basis="estimated" if pricing_note else "inferred",
        price_source=entry["source"],
        price_as_of=entry["as_of"],
        quantity={
            "input": input_tokens,
            "cache_write": usage.get("cache_write"),
            "cache_write_5m": usage.get("cache_write_5m"),
            "cache_write_1h": usage.get("cache_write_1h"),
            "cached_input": cached_tokens,
            "output": output_tokens,
            "token_accounting_source": usage.get("accounting_source"),
            "token_source_kind": usage.get("source_kind"),
        },
        assumed_rate={
            "input_usd_per_mtok": input_rate,
            # A single-write-rate model (OpenAI) is labelled with that one rate,
            # never with Anthropic-style TTL keys it does not publish.
            **(
                {"cache_write_usd_per_mtok": single_write_rate}
                if single_write_rate is not None
                else {
                    "cache_write_5m_usd_per_mtok": write_5m_rate,
                    "cache_write_1h_usd_per_mtok": write_1h_rate,
                }
            ),
            # Only claim a write-rate source when writes were actually priced.
            "cache_write_rate_source": (
                "published_cache_write_rate" if declared_write_rates else "base_input_fallback"
            )
            if writes
            else None,
            "cached_input_usd_per_mtok": cached_rate,
            "output_usd_per_mtok": output_rate,
            "rate_basis": entry.get("rate_basis"),
        },
    )
    if pricing_note:
        component["pricing_note"] = pricing_note
    return component


def cell_cost(
    *,
    vendor_calls: Iterable[Mapping[str, Any]] = (),
    table: PriceTable,
    has_harness: bool = True,
    model_id: str | None = None,
    token_usage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the cost record for one cell.

    ``has_harness=False`` is for vendor-agent arms, where no harness model is in
    the loop; a harness arm whose model or tokens are missing is unknown, not
    free.
    """

    components = [vendor_call_cost(call, table) for call in vendor_calls]
    if has_harness:
        components.append(model_cost(model_id, token_usage, table))
    return {
        "currency": "USD",
        "total_usd": _subtotal(components),
        "vendor_usd": _subtotal([c for c in components if c["kind"] == "vendor"]),
        "model_usd": _subtotal([c for c in components if c["kind"] == "model"]),
        "basis": _rollup_basis(components),
        "components": components,
        "unknown_reasons": [c["reason"] for c in components if c["basis"] == "unknown"],
    }


def summarize_arm_costs(cells: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate cell cost records per arm.

    Each cell is ``{"arm": str, "success": bool, "cost": <cell_cost record>}``.
    Cells with unknown cost are excluded from numerator and denominator alike,
    so ``usd_per_success`` is spend on costed cells over successes among those
    same cells; the exclusion is counted in ``unknown_cost_cells``.
    """

    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for cell in cells:
        grouped.setdefault(str(cell["arm"]), []).append(cell)
    return {arm: _summarize_arm(arm, arm_cells) for arm, arm_cells in sorted(grouped.items())}


def render_cost_table(summaries: Mapping[str, Mapping[str, Any]]) -> str:
    """Render the Markdown cost table; arms with unknown cost are listed, never priced."""

    lines = [
        "| arm | costed cells | successes | $/task | $/success | basis |",
        "|---|---:|---:|---:|---:|---|",
    ]
    warnings: list[str] = []
    for arm, summary in summaries.items():
        if not summary["in_cost_table"]:
            warnings.append(
                f"- ⚠ {arm}: excluded from cost table, cost unknown for all "
                f"{summary['cells']} cells ({_reason_text(summary)})"
            )
            continue
        if summary["unknown_cost_cells"]:
            warnings.append(
                f"- ⚠ {arm}: {summary['unknown_cost_cells']} of {summary['cells']} cells "
                f"excluded, cost unknown ({_reason_text(summary)})"
            )
        per_success = (
            f"${summary['usd_per_success']:.4f}"
            if summary["usd_per_success"] is not None
            else "undefined (0 successes)"
        )
        lines.append(
            f"| {arm} | {summary['costed_cells']}/{summary['cells']} | {summary['successes']} "
            f"| ${summary['usd_per_task']:.4f} | {per_success} | {summary['basis']} |"
        )
    sources = sorted(
        {
            (source["price_source"], source["price_as_of"])
            for summary in summaries.values()
            for source in summary["price_sources"]
        }
    )
    out = lines
    if warnings:
        out = out + ["", *warnings]
    if sources:
        out = out + ["", "Price sources:", *(f"- {src} (as of {as_of})" for src, as_of in sources)]
    return "\n".join(out) + "\n"


def _summarize_arm(arm: str, cells: list[Mapping[str, Any]]) -> dict[str, Any]:
    costed = [cell for cell in cells if cell["cost"]["total_usd"] is not None]
    unknown = [cell for cell in cells if cell["cost"]["total_usd"] is None]
    successes = sum(1 for cell in costed if cell.get("success") is True)
    total = sum(float(cell["cost"]["total_usd"]) for cell in costed) if costed else None
    if not costed:
        status = "unknown_cost"
        per_success = None
    elif successes == 0:
        status = "undefined_zero_successes"
        per_success = None
    else:
        status = "defined"
        per_success = total / successes
    sources = {
        (component["price_source"], component["price_as_of"])
        for cell in costed
        for component in cell["cost"]["components"]
        if component["price_source"] not in (None, "provider_response", "no_request_issued")
    }
    reasons = sorted({reason for cell in unknown for reason in cell["cost"]["unknown_reasons"]})
    return {
        "arm": arm,
        "cells": len(cells),
        "costed_cells": len(costed),
        "unknown_cost_cells": len(unknown),
        "successes": successes,
        "total_usd": total,
        "usd_per_task": total / len(costed) if costed else None,
        "usd_per_success": per_success,
        "usd_per_success_status": status,
        "basis": _rollup_basis([{"basis": cell["cost"]["basis"]} for cell in costed])
        if costed
        else "unknown",
        "price_sources": [{"price_source": s, "price_as_of": t} for s, t in sorted(sources)],
        "unknown_reasons": reasons,
        "in_cost_table": bool(costed),
    }


def _reason_text(summary: Mapping[str, Any]) -> str:
    return ", ".join(summary["unknown_reasons"]) or "no reason recorded"


def _component(
    kind: str,
    subject: str,
    *,
    amount: float,
    basis: str,
    price_source: str,
    price_as_of: str | None,
    quantity: Mapping[str, Any],
    assumed_rate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "subject": subject,
        "amount_usd": amount,
        "basis": basis,
        "price_source": price_source,
        "price_as_of": price_as_of,
        "quantity": dict(quantity),
        "assumed_rate": dict(assumed_rate) if assumed_rate else None,
        "reason": None,
    }


def _unknown(kind: str, subject: str, reason: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "subject": subject,
        "amount_usd": None,
        "basis": "unknown",
        "price_source": None,
        "price_as_of": None,
        "quantity": None,
        "assumed_rate": None,
        "reason": f"{kind}:{subject}:{reason}",
    }


def _subtotal(components: list[Mapping[str, Any]]) -> float | None:
    if any(component["amount_usd"] is None for component in components):
        return None
    return float(sum(component["amount_usd"] for component in components))


def _rollup_basis(components: Iterable[Mapping[str, Any]]) -> str:
    bases = {component["basis"] for component in components}
    if "unknown" in bases:
        return "unknown"
    if "estimated" in bases:
        return "estimated"
    if "inferred" in bases:
        return "inferred"
    return "measured"


def _reported_usd(cost: Mapping[str, Any] | None) -> float | None:
    if not isinstance(cost, Mapping):
        return None
    if cost.get("currency") not in (None, "USD"):
        return None
    total = cost.get("total")
    if isinstance(total, bool) or not isinstance(total, (int, float)) or total < 0:
        return None
    return float(total)


def _credits_used(units: Mapping[str, Any] | None) -> float | None:
    if not isinstance(units, Mapping):
        return None
    for key in ("credits_used", "creditsUsed"):
        value = units.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return float(value)
    return None


def _entries(document: Mapping[str, Any], key: str) -> dict[str, Mapping[str, Any]]:
    section = document.get(key)
    if not isinstance(section, Mapping):
        raise PriceTableError(f"price table {key} must be a mapping")
    for name, entry in section.items():
        if not isinstance(entry, Mapping):
            raise PriceTableError(f"{key}.{name} must be a mapping")
    return {str(name): dict(entry) for name, entry in section.items()}


def _require_receipt(entry: Mapping[str, Any], where: str) -> None:
    for field in ("source", "as_of"):
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            raise PriceTableError(f"{where}.{field} is required: a rate without a receipt")
    try:
        date.fromisoformat(entry["as_of"])
    except ValueError as exc:
        raise PriceTableError(f"{where}.as_of must be an ISO date") from exc
    if entry.get("rate_basis") not in RATE_BASES:
        raise PriceTableError(f"{where}.rate_basis must be one of {sorted(RATE_BASES)}")
    if entry["rate_basis"] == "self-hosted" and any(
        value != 0 for key, value in entry.items() if "usd_per_" in key and value is not None
    ):
        raise PriceTableError(f"{where} self-hosted rates must be $0")
    proxy_for = entry.get("proxy_for")
    if entry["rate_basis"] == "operator_proxy_rate" and (
        not isinstance(proxy_for, str) or not proxy_for.strip()
    ):
        raise PriceTableError(f"{where}.proxy_for is required: a proxy rate names its vendor")


def _require_rate(entry: Mapping[str, Any], field: str, where: str) -> None:
    value = entry.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise PriceTableError(f"{where}.{field} must be a non-negative number")
