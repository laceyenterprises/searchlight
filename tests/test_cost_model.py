import json
import math

import pytest

from sew.cost_model import (
    PriceTableError,
    cell_cost,
    load_price_table,
    model_cost,
    parse_price_table,
    render_cost_table,
    summarize_arm_costs,
    vendor_call,
    vendor_call_cost,
)

ENDED = "2026-09-25T18:00:01Z"


@pytest.fixture(scope="module")
def table():
    return load_price_table()


def measured_tokens(input_tokens=1000, cached=0, output=500):
    return {
        "accounting_source": "measured",
        "source_kind": "session_ledger",
        "input": input_tokens,
        "cache_write": 0,
        "cached_input": cached,
        "output": output,
        "reasoning": None,
        "total_billable": input_tokens + output,
    }


def exa_call(total=0.005):
    return vendor_call(
        provider_id="exa",
        status="ok",
        ended_at=ENDED,
        provider_cost={"currency": "USD", "total": total},
    )


def test_shipped_price_table_loads_and_every_entry_has_a_receipt(table):
    for entry in [*table.vendors.values(), *table.models.values()]:
        assert entry["source"].startswith("https://")
        assert entry["as_of"]


def test_price_entry_without_source_or_timestamp_is_refused():
    base = {
        "version": 1,
        "vendors": {},
        "models": {
            "m": {
                "input_usd_per_mtok": 1.0,
                "output_usd_per_mtok": 2.0,
                "rate_basis": "published_list_price",
                "source": "https://example.test/pricing",
                "as_of": "2026-09-25",
            }
        },
    }
    parse_price_table(base)
    for missing in ("source", "as_of"):
        broken = json.loads(json.dumps(base))
        del broken["models"]["m"][missing]
        with pytest.raises(PriceTableError, match=missing):
            parse_price_table(broken)
    broken = json.loads(json.dumps(base))
    broken["models"]["m"]["as_of"] = "last tuesday"
    with pytest.raises(PriceTableError, match="ISO date"):
        parse_price_table(broken)

    for key, value in (
        ("cache_write_usd_per_mtok", -2.5),
        ("max_short_context_input_tokens", "272K"),
        ("max_short_context_input_tokens", True),
        ("max_short_context_input_tokens", 0),
    ):
        broken = json.loads(json.dumps(base))
        broken["models"]["m"][key] = value
        with pytest.raises(PriceTableError, match=key):
            parse_price_table(broken)


def test_operator_proxy_rate_must_name_the_vendor_it_stands_in_for(table):
    """A proxy price (codex-native at Brave's rate) is a decision, so it names its source."""

    codex = table.vendors["codex-native"]
    assert (codex["rate_basis"], codex["proxy_for"]) == ("operator_proxy_rate", "brave")
    assert codex["usd_per_request"] == table.vendors["brave"]["usd_per_request"] | {
        "fetch:request": 0.0
    }
    proxy = vendor_call_cost(
        vendor_call(
            provider_id="codex-native",
            status="ok",
            ended_at=ENDED,
            pricing_tier="search:request",
        ),
        table,
    )
    assert proxy["assumed_rate"]["proxy_for"] == "brave"
    assert proxy["pricing_note"] == "proxy_rate:codex-native->brave"
    base = {
        "version": 1,
        "vendors": {
            "v": {
                "meter": "requests",
                "rate_basis": "operator_proxy_rate",
                "proxy_for": "brave",
                "source": "https://example.test/pricing",
                "as_of": "2026-09-30",
                "usd_per_request": {"search:request": 0.005},
            }
        },
        "models": {},
    }
    base["vendors"]["brave"] = {**base["vendors"]["v"], "rate_basis": "published_list_price"}
    parse_price_table(base)
    for bad in (None, "", "  ", "brvae", "v"):
        broken = json.loads(json.dumps(base))
        broken["vendors"]["v"]["proxy_for"] = bad
        with pytest.raises(PriceTableError, match="proxy_for"):
            parse_price_table(broken)


def test_exa_reported_dollars_are_measured_and_stamped_with_the_call_time(table):
    component = vendor_call_cost(exa_call(0.007), table)

    assert component["basis"] == "measured"
    assert component["amount_usd"] == pytest.approx(0.007)
    assert component["price_source"] == "provider_response"
    assert component["price_as_of"] == ENDED


def test_credit_to_dollar_conversion_carries_its_assumed_rate(table):
    call = vendor_call(
        provider_id="firecrawl",
        status="ok",
        ended_at=ENDED,
        provider_units={"credits_used": 12},
    )

    component = vendor_call_cost(call, table)

    assert component["basis"] == "inferred"
    assert component["quantity"] == {"credits": 12.0}
    rate = component["assumed_rate"]
    assert rate["usd_per_credit"] == table.vendors["firecrawl"]["usd_per_credit"]
    assert rate["rate_basis"] == "plan_rate_assumption"
    assert rate["plan"]
    assert component["amount_usd"] == pytest.approx(12 * rate["usd_per_credit"])
    assert component["price_source"] == table.vendors["firecrawl"]["source"]


def test_agent_lane_credits_used_spelling_is_also_converted(table):
    call = vendor_call(
        provider_id="firecrawl", status="ok", ended_at=ENDED, provider_units={"creditsUsed": 30}
    )

    assert vendor_call_cost(call, table)["basis"] == "inferred"


def test_parallel_list_price_is_inferred_only_for_a_declared_tier(table):
    priced = vendor_call_cost(
        vendor_call(
            provider_id="parallel-web", status="ok", ended_at=ENDED, pricing_tier="task:core"
        ),
        table,
    )
    assert priced["basis"] == "inferred"
    assert priced["amount_usd"] == pytest.approx(0.025)
    assert priced["assumed_rate"]["rate_basis"] == "published_list_price"

    unguessed = vendor_call_cost(
        vendor_call(provider_id="parallel-web", status="ok", ended_at=ENDED), table
    )
    assert unguessed["basis"] == "unknown"
    assert unguessed["amount_usd"] is None


def test_provider_that_reports_nothing_and_has_no_price_is_unknown_not_zero(table):
    component = vendor_call_cost(
        vendor_call(provider_id="brave", status="ok", ended_at=ENDED), table
    )

    assert component["basis"] == "unknown"
    assert component["amount_usd"] is None
    assert "brave" in component["reason"]


def test_failed_call_without_reported_spend_is_unknown(table):
    component = vendor_call_cost(
        vendor_call(
            provider_id="parallel-web",
            status="rate_limited",
            ended_at=ENDED,
            pricing_tier="search:fast",
        ),
        table,
    )

    assert component["basis"] == "unknown"


def test_model_cost_prices_measured_tokens_at_published_rates(table):
    component = model_cost("claude-opus-5", measured_tokens(1_000_000, 1_000_000, 100_000), table)

    rates = table.models["claude-opus-5"]
    expected = (
        rates["input_usd_per_mtok"]
        + rates["cached_input_usd_per_mtok"]
        + 0.1 * rates["output_usd_per_mtok"]
    )
    assert component["amount_usd"] == pytest.approx(expected)
    assert component["basis"] == "inferred"
    assert component["quantity"]["token_accounting_source"] == "measured"
    assert component["price_as_of"] == rates["as_of"]


def test_opus_55_context_variant_uses_only_allowlisted_standard_rate(table):
    usage = measured_tokens(1_000_000, 1_000_000, 100_000)
    priced = model_cost("claude-opus-5-5[1m]", usage, table)
    assert priced["amount_usd"] == pytest.approx(4 + 0.2 + 2)
    assert model_cost("claude-opus-5-5[2m]", usage, table)["reason"].endswith("no_published_rate")


def test_opus_55_cache_writes_use_ttl_rates_or_explicit_estimate(table):
    usage = measured_tokens(1_000_000, 200_000, 100_000)
    usage.update(cache_write=600_000, cache_write_5m=400_000, cache_write_1h=200_000)
    priced = model_cost("claude-opus-5-5", usage, table)
    assert priced["amount_usd"] == pytest.approx(0.4 * 4 + 0.4 * 5 + 0.2 * 8 + 0.2 * 0.2 + 0.1 * 20)
    assert priced["basis"] == "inferred"
    assert priced["quantity"]["cache_write"] == 600_000

    del usage["cache_write_5m"], usage["cache_write_1h"]
    estimated = model_cost("claude-opus-5-5", usage, table)
    assert estimated["amount_usd"] == pytest.approx(0.4 * 4 + 0.6 * 5 + 0.2 * 0.2 + 0.1 * 20)
    assert estimated["basis"] == "estimated"
    assert estimated["pricing_note"] == "cache_write_ttl_unknown_priced_at_5m_rate"

    del usage["cache_write"]
    legacy = model_cost("claude-opus-5-5", usage, table)
    assert legacy["basis"] == "estimated"
    assert legacy["pricing_note"] == "cache_writes_priced_at_base_input"

    usage["cache_write"] = 600_000
    no_receipt = model_cost("claude-opus-5", usage, table)
    assert no_receipt["basis"] == "estimated"
    assert no_receipt["pricing_note"] == "cache_write_rate_missing_priced_at_base_input"


def test_estimated_credit_record_without_request_count_remains_priceable(table):
    call = vendor_call(
        provider_id="tavily",
        status="ok",
        ended_at=ENDED,
        provider_units={"credits_used": 2, "credits_source": "estimated_from_request"},
    )
    component = vendor_call_cost(call, table)
    assert component["basis"] == "estimated"
    assert component["quantity"] == {"credits": 2}


def test_claude_native_search_and_fetch_prices(table):
    search = vendor_call_cost(
        vendor_call(
            provider_id="claude-code-native",
            status="ok",
            ended_at=ENDED,
            pricing_tier="search:request",
        ),
        table,
    )
    fetch = vendor_call_cost(
        vendor_call(
            provider_id="claude-code-native",
            status="ok",
            ended_at=ENDED,
            pricing_tier="fetch:request",
        ),
        table,
    )
    assert search["amount_usd"] == pytest.approx(0.01)
    assert fetch["amount_usd"] == 0


def test_failed_brave_tool_call_without_http_status_stays_unpriced(table):
    component = vendor_call_cost(
        vendor_call(provider_id="brave", status="failed", ended_at=ENDED), table
    )
    assert component["basis"] == "unknown"
    assert component["reason"] == "vendor:brave:unreported_spend_on_failed"


def test_codex_model_prices_only_with_known_id(table):
    assert model_cost("gpt-6-sol", measured_tokens(), table)["amount_usd"] == pytest.approx(0.007)
    assert (
        model_cost(None, measured_tokens(), table)["reason"]
        == "model:unknown-model:model_id_unknown"
    )
    assert model_cost("gpt-6-sol", measured_tokens(300_000), table)["reason"] == (
        "model:gpt-6-sol:long_context_rate_requires_per_request_usage"
    )
    writes = model_cost("gpt-6-sol", {**measured_tokens(), "cache_write": 100}, table)
    assert writes["amount_usd"] == pytest.approx(0.00705)
    assert writes["basis"] == "inferred"
    assert "pricing_note" not in writes
    unreported = measured_tokens()
    del unreported["cache_write"]
    assert model_cost("gpt-6-sol", unreported, table)["basis"] == "estimated"


@pytest.mark.parametrize(
    ("model_id", "usage", "reason"),
    [
        (None, measured_tokens(), "model_id_unknown"),
        ("claude-opus-5", {"accounting_source": "unknown", "input": None}, "token_usage_unknown"),
        ("gpt-5.5", measured_tokens(), "no_published_rate"),
    ],
)
def test_model_cost_is_unknown_rather_than_guessed(table, model_id, usage, reason):
    component = model_cost(model_id, usage, table)

    assert component["amount_usd"] is None
    assert component["reason"].endswith(reason)


def test_price_source_timestamps_present_on_every_priced_component(table):
    record = cell_cost(
        vendor_calls=[
            exa_call(),
            vendor_call(
                provider_id="tavily",
                status="ok",
                ended_at=ENDED,
                provider_units={"credits_used": 1},
            ),
            vendor_call(
                provider_id="parallel-web",
                status="ok",
                ended_at=ENDED,
                pricing_tier="search:basic",
            ),
        ],
        table=table,
        model_id="claude-sonnet-5",
        token_usage=measured_tokens(),
    )

    assert record["basis"] == "inferred"
    assert record["total_usd"] is not None
    for component in record["components"]:
        assert component["basis"] in {"measured", "inferred"}
        assert component["price_source"]
        assert component["price_as_of"]


def test_cell_with_any_unknown_component_has_unknown_total(table):
    record = cell_cost(
        vendor_calls=[exa_call()], table=table, model_id="gpt-5.5", token_usage=measured_tokens()
    )

    assert record["total_usd"] is None
    assert record["vendor_usd"] == pytest.approx(0.005)
    assert record["model_usd"] is None
    assert record["basis"] == "unknown"
    assert record["unknown_reasons"]


def test_vendor_agent_arm_without_a_harness_can_be_fully_measured(table):
    record = cell_cost(vendor_calls=[exa_call(0.1)], table=table, has_harness=False)

    assert record["basis"] == "measured"
    assert record["total_usd"] == pytest.approx(0.1)


def _cell(arm, success, record):
    return {"arm": arm, "success": success, "cost": record}


def test_arm_with_unknown_cost_is_excluded_from_cost_table_rather_than_shown_as_zero(table):
    unknown = cell_cost(
        vendor_calls=[vendor_call(provider_id="brave", status="ok", ended_at=ENDED)],
        table=table,
        has_harness=False,
    )
    known = cell_cost(vendor_calls=[exa_call(0.02)], table=table, has_harness=False)
    summaries = summarize_arm_costs(
        [
            _cell("brave-arm", True, unknown),
            _cell("brave-arm", True, unknown),
            _cell("exa-arm", True, known),
        ]
    )

    brave = summaries["brave-arm"]
    assert brave["in_cost_table"] is False
    assert brave["total_usd"] is None
    assert brave["usd_per_task"] is None
    assert brave["usd_per_success"] is None
    assert brave["usd_per_success_status"] == "unknown_cost"

    rendered = render_cost_table(summaries)
    table_rows = [line for line in rendered.splitlines() if line.startswith("| brave-arm")]
    assert table_rows == []
    assert "brave-arm: excluded from cost table" in rendered
    assert "$0.0000" not in rendered
    assert "| exa-arm |" in rendered


def test_unknown_cost_cells_are_excluded_from_both_sides_of_cost_per_success(table):
    known = cell_cost(vendor_calls=[exa_call(0.04)], table=table, has_harness=False)
    unknown = cell_cost(
        vendor_calls=[vendor_call(provider_id="brave", status="ok", ended_at=ENDED)],
        table=table,
        has_harness=False,
    )
    summary = summarize_arm_costs(
        [
            _cell("a", True, known),
            _cell("a", False, known),
            _cell("a", True, unknown),
        ]
    )["a"]

    assert summary["costed_cells"] == 2
    assert summary["unknown_cost_cells"] == 1
    assert summary["successes"] == 1
    assert summary["usd_per_success"] == pytest.approx(0.08)
    assert "1 of 3 cells excluded" in render_cost_table({"a": summary})


def test_cost_per_success_is_undefined_not_infinite_with_zero_successes(table):
    record = cell_cost(vendor_calls=[exa_call(0.001)], table=table, has_harness=False)
    summaries = summarize_arm_costs([_cell("cheap", False, record), _cell("cheap", False, record)])

    cheap = summaries["cheap"]
    assert cheap["usd_per_success"] is None
    assert cheap["usd_per_success_status"] == "undefined_zero_successes"
    assert cheap["usd_per_task"] == pytest.approx(0.001)
    assert cheap["in_cost_table"] is True
    # Serializes as strict JSON: no Infinity/NaN can be smuggled through.
    json.dumps(summaries, allow_nan=False)
    assert not any(isinstance(value, float) and math.isinf(value) for value in cheap.values())
    assert "undefined (0 successes)" in render_cost_table(summaries)


def test_cost_per_success_penalizes_the_cheap_arm_that_fails_often(table):
    cheap = cell_cost(vendor_calls=[exa_call(0.01)], table=table, has_harness=False)
    dear = cell_cost(vendor_calls=[exa_call(0.03)], table=table, has_harness=False)
    summaries = summarize_arm_costs(
        [_cell("cheap", i == 0, cheap) for i in range(10)]
        + [_cell("dear", i < 9, dear) for i in range(10)]
    )

    assert summaries["cheap"]["usd_per_task"] < summaries["dear"]["usd_per_task"]
    assert summaries["cheap"]["usd_per_success"] > summaries["dear"]["usd_per_success"]


def test_cost_table_lists_price_sources_with_dates(table):
    record = cell_cost(
        vendor_calls=[
            vendor_call(
                provider_id="firecrawl",
                status="ok",
                ended_at=ENDED,
                provider_units={"credits_used": 5},
            )
        ],
        table=table,
        model_id="claude-haiku-4-5",
        token_usage=measured_tokens(),
    )
    rendered = render_cost_table(summarize_arm_costs([_cell("fc", True, record)]))

    assert "Price sources:" in rendered
    assert f"{table.vendors['firecrawl']['source']} (as of " in rendered
    assert f"{table.models['claude-haiku-4-5']['source']} (as of " in rendered
    assert "| fc | 1/1 | 1 |" in rendered
    assert "inferred" in rendered


def test_live_codex_usage_prices_as_a_labelled_estimate() -> None:
    """End to end from a real codex turn.completed usage block.

    Codex never reports cache writes, so writes stay inside input at the base
    rate: an estimate, labelled with the one write rate OpenAI publishes and
    no write-rate source (none were priced).
    """

    from sew.bakeoff_report import _priceable_usage
    from sew.live_harness import _codex_usage_row
    from sew.token_accounting import account_tokens

    row = _codex_usage_row(
        [
            {
                "input_tokens": 50_000,
                "cached_input_tokens": 30_000,
                "output_tokens": 4_000,
                "reasoning_output_tokens": 1_000,
            }
        ]
    )
    usage = account_tokens(harness_usage_rows=[row])
    priceable, reason = _priceable_usage(usage)
    assert reason is None
    cost = model_cost("gpt-6-sol", priceable, load_price_table())
    assert cost["basis"] == "estimated"
    assert cost["pricing_note"] == "cache_writes_priced_at_base_input"
    rates = cost["assumed_rate"]
    assert rates["cache_write_usd_per_mtok"] == 2.5
    assert "cache_write_5m_usd_per_mtok" not in rates
    assert rates["cache_write_rate_source"] is None


def test_price_entry_with_single_and_ttl_write_rates_is_refused() -> None:
    base = {
        "version": 1,
        "vendors": {},
        "models": {
            "mixed": {
                "input_usd_per_mtok": 1.0,
                "output_usd_per_mtok": 2.0,
                "cache_write_usd_per_mtok": 1.25,
                "cache_write_5m_usd_per_mtok": 1.25,
                "rate_basis": "published_list_price",
                "source": "https://example.test/pricing",
                "as_of": "2026-09-27",
            }
        },
    }
    with pytest.raises(PriceTableError, match="both cache_write_usd_per_mtok and TTL"):
        parse_price_table(base)
