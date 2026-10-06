"""VENDORPRICE-01: no vendor tool may silently disappear from pricing."""

import json
from types import SimpleNamespace

import pytest
import yaml

from conftest import MODULE_ROOT
from sew.arms import PROVIDER_SERVER_NAMES
from sew.cost_model import load_price_table, vendor_call, vendor_call_cost
from sew.mcp_meter import metered_server_config, parallel_search_settings
from sew.pricing_coverage import (
    configured_pricing_tools,
    pricing_coverage,
    require_pricing_parity,
    render_pricing_coverage,
)
from sew.runner import load_provider_exposures, RunnerError
from test_mcp_meter import _call, _meter, _text


FIXTURES = json.loads((MODULE_ROOT / "fixtures/pricing/vendor-calls.json").read_text())


def _price(record):
    response = record["response"]
    return vendor_call_cost(
        vendor_call(
            provider_id=record["provider_id"],
            status=record["status"],
            ended_at=record["ended_at"],
            provider_cost=response.get("provider_cost"),
            provider_units=response.get("provider_units"),
            pricing_tier=response.get("pricing_tier"),
        ),
        load_price_table(),
    )


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f["tool"])
def test_vendor_call_fixtures(tmp_path, fixture):
    record = _call(
        _meter(tmp_path, fixture["provider"]),
        fixture["tool"],
        fixture["arguments"],
        fixture["response"],
    )
    price = _price(record)
    assert record["operation"] == fixture["tool"]
    assert price["amount_usd"] == pytest.approx(fixture["expected_usd"])
    assert price["basis"] == fixture["expected_basis"]
    assert price["price_as_of"] == "2026-10-05"
    assert price["price_source"] in {"https://exa.ai/pricing", "https://parallel.ai/pricing"}
    assert "example.test" not in json.dumps(record)


def test_exa_receipt_wins_and_server_default_is_observed(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFAULT_SEARCH_TYPE", "instant")
    meter = _meter(tmp_path, "exa")
    fallback = _call(meter, "web_search_exa", {}, {"content": [{"type": "text", "text": "result"}]})
    assert _price(fallback)["amount_usd"] == 0.004
    ignored_type = _call(meter, "web_search_exa", {"type": "auto"}, _text({}))
    # Published 3.4.1 registerWebSearchTool accepts query/numResults only;
    # its API request uses config.defaultSearchType, even with an extra type.
    assert _price(ignored_type)["amount_usd"] == 0.004
    measured = _call(meter, "web_search_exa", {}, _text({"costDollars": {"total": 0.123}}))
    assert _price(measured)["amount_usd"] == 0.123
    assert _price(measured)["basis"] == "measured"


@pytest.mark.parametrize(
    "provider,tool,arg",
    [
        ("parallel-web", "web_search", "mode"),
    ],
)
def test_new_modes_are_unpriced(tmp_path, provider, tool, arg):
    record = _call(_meter(tmp_path, provider), tool, {arg: "new-mode"}, _text({}))
    assert record["response"]["meter"]["unpriced_reason"] == f"unrecognized_argument:{arg}"
    assert _price(record)["amount_usd"] is None


def test_parallel_missing_mode_is_explicit(tmp_path):
    record = _call(_meter(tmp_path, "parallel-web"), "web_search", {}, _text({}))
    assert record["operation"] == "web_search"
    assert record["response"]["meter"]["unpriced_reason"] == "search_mode_not_observed"


def test_parallel_connection_mode_and_count_override_call_args(tmp_path, monkeypatch):
    wrapped = metered_server_config(
        {
            "command": "npx",
            "args": [
                "mcp-remote",
                "https://search.parallel.ai/mcp-oauth?mode=basic&advanced_settings.max_results=11",
            ],
        },
        provider_id="parallel-web",
        run_id="r",
        call_dir=tmp_path,
    )
    monkeypatch.setenv(
        "SEW_METER_PARALLEL_SEARCH_CONFIG", wrapped["env"]["SEW_METER_PARALLEL_SEARCH_CONFIG"]
    )
    record = _call(
        _meter(tmp_path, "parallel-web"),
        "web_search",
        {"mode": "fast", "max_results": 5},
        _text({"results": [{}] * 11}),
    )
    assert _price(record)["amount_usd"] == pytest.approx(0.006)
    assert record["request"]["pricing_arguments"]["mode"] == "fast"
    assert record["request"]["pricing_arguments"]["max_results"] == 5
    assert record["response"]["meter"]["connection_override"] == {
        "mode": "basic",
        "max_results": 11,
    }


@pytest.mark.parametrize("transport", ["headers", "header", "header-file", "header_from_env"])
def test_parallel_header_overrides_are_secret_free(tmp_path, monkeypatch, transport):
    raw = json.dumps(
        {
            "mode": "basic",
            "advanced_settings": {"max_results": 11},
            "objective": "private-objective",
            "credential": "private-credential",
        }
    )
    config = {"command": "npx", "args": ["mcp-remote", "https://search.parallel.ai/mcp-oauth"]}
    if transport == "headers":
        config["headers"] = {"X-Parallel-Search-Config": raw}
    elif transport == "header":
        config["args"] += ["--header", "X-Parallel-Search-Config:" + raw]
    elif transport == "header-file":
        path = tmp_path / "headers.txt"
        path.write_text("Authorization: private-token\nX-Parallel-Search-Config: " + raw + "\n")
        config["args"] += ["--header-file", str(path)]
    else:
        config["header_from_env"] = {"name": "X-Parallel-Search-Config", "value": raw}
    settings = parallel_search_settings(config)
    assert settings == {"mode": "basic", "max_results": 11}
    monkeypatch.setenv("SEW_METER_PARALLEL_SEARCH_CONFIG", json.dumps(settings))
    record = _call(
        _meter(tmp_path, "parallel-web"), "web_search", {}, _text({"results": [{}] * 11})
    )
    assert _price(record)["amount_usd"] == pytest.approx(0.006)
    assert record["request"]["argument_keys"] == []
    assert record["request"]["unrecognized_argument_key_count"] == 0
    assert record["request"]["pricing_arguments"] == {}
    assert record["response"]["meter"]["connection_override"] == settings
    assert "private-" not in json.dumps(record)
    config["args"][1] += "?mode=fast&advanced_settings.max_results=5"
    assert parallel_search_settings(config) == {"mode": "fast", "max_results": 5}


@pytest.mark.parametrize("mode", [["basic"], {"mode": "basic"}])
def test_parallel_non_string_connection_mode_is_unpriced(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("SEW_METER_PARALLEL_SEARCH_CONFIG", json.dumps({"mode": mode}))
    record = _call(_meter(tmp_path, "parallel-web"), "web_search", {}, _text({}))
    assert record["response"]["meter"]["unpriced_reason"] == "connection_search_mode_unknown"
    assert record["response"]["meter"]["connection_override"] == {"mode": "<unrecognized>"}


def test_additional_results_require_a_count_and_page_counts_are_not_free(tmp_path):
    meter = _meter(tmp_path, "exa")
    unknown = _call(meter, "web_search_exa", {"numResults": 11}, {"content": []})
    assert unknown["response"]["meter"]["unpriced_reason"] == "additional_result_count_unknown"
    known = _call(meter, "web_search_exa", {"numResults": 11}, _text({"results": [{}] * 11}))
    assert _price(known)["amount_usd"] == pytest.approx(0.008)
    missing_pages = _call(meter, "web_fetch_exa", {}, _text({}))
    assert missing_pages["response"]["meter"]["unpriced_reason"] == "page_count_unknown"


def test_every_configured_provider_arm_tool_has_a_pricing_contract():
    manifest = yaml.safe_load((MODULE_ROOT / "config/provider-arm-tools.yaml").read_text())
    providers = manifest["providers"]
    assert set(providers) == set(PROVIDER_SERVER_NAMES)
    assert all(p["command"] and p["args"] and p["enabled_tools"] for p in providers.values())
    require_pricing_parity({p: c["enabled_tools"] for p, c in providers.items()})


def test_parity_gate_rejects_a_missing_rule_and_accepts_documented_receipts():
    with pytest.raises(ValueError, match="exa:web_search_exa"):
        require_pricing_parity({"exa": ["web_search_exa"]}, {"exa": {"other_tool": {}}})
    require_pricing_parity(
        {"exa": ["web_search_exa"]}, {}, {"exa": {"web_search_exa": "response.costDollars.total"}}
    )


def test_arm_config_cannot_enable_a_tool_without_a_pricing_contract(tmp_path):
    path = tmp_path / "mcp.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "exa": {
                    "command": "npx",
                    "args": ["exa-mcp-server@3.4.1"],
                    "env": {"ENABLED_TOOLS": "${TOOLS_SELECTION}"},
                }
            }
        )
    )
    with pytest.raises(RunnerError, match="pricing parity missing: exa:new_tool"):
        load_provider_exposures(path, {"TOOLS_SELECTION": "web_search_exa,new_tool"})
    assert "exa" in load_provider_exposures(
        path, {"TOOLS_SELECTION": "web_search_exa,web_fetch_exa"}
    )


def test_discovery_reports_missing_pricing_without_publishing_unknown_names(tmp_path):
    meter = _meter(tmp_path, "exa")
    meter.client_message({"id": 1, "method": "tools/list"})
    meter.server_message(
        {
            "id": 1,
            "result": {
                "tools": [
                    {"name": "web_search_exa"},
                    {"name": "sensitive-new-tool"},
                ]
            },
        }
    )
    availability = json.loads((tmp_path / "provider-calls/availability.json").read_text())
    assert availability["pricing_missing_tools_n"] == 1
    assert "sensitive-new-tool" not in json.dumps(availability)
    for request_id, names in [(2, ["sensitive-new-tool"]), (3, ["another-private-tool"])]:
        meter.client_message({"id": request_id, "method": "tools/list"})
        meter.server_message({"id": request_id, "result": {"tools": [{"name": n} for n in names]}})
    availability = json.loads((tmp_path / "provider-calls/availability.json").read_text())
    assert availability["pricing_missing_tools_n"] == 2
    assert "private-tool" not in json.dumps(availability)
    assert "sensitive-new-tool" not in json.dumps(availability)


def test_coverage_counts_calls_even_without_model_tokens_and_flags_missing_metering(tmp_path):
    records = []
    for i, args in enumerate(({"mode": "fast"}, {})):
        record = _call(_meter(tmp_path, "parallel-web"), "web_search", args, _text({}))
        path = tmp_path / f"call-{i}.json"
        path.write_text(json.dumps(record))
        records.append(path.name)
    (tmp_path / "run.json").write_text(json.dumps({"provider_call_refs": records}))
    row = SimpleNamespace(provider_id="parallel-web", run_id="run-1", run_dir=tmp_path)
    coverage = pricing_coverage([row], load_price_table())
    own = coverage["parallel-web"]
    assert own["priced_n"] == own["unpriced_n"] == 1
    assert own["coverage"] == 0.5
    assert own["lower_bound"] is True
    assert own["unpriced_reasons"] == {"search_mode_not_observed": 1}
    assert "1/2" in render_pricing_coverage(coverage)
    assert (
        pricing_coverage(
            [SimpleNamespace(provider_id="exa", run_id="missing", run_dir=None)], load_price_table()
        )["exa"]["coverage"]
        is None
    )


@pytest.mark.parametrize(
    "provider,tool,arg,args",
    [
        ("exa", "web_search_exa", "numResults", {}),
        ("parallel-web", "web_search", "max_results", {"mode": "fast"}),
    ],
)
@pytest.mark.parametrize("limit", [25, 0, -1, True, None, "private-limit", 25.5])
def test_result_limit_evidence_survives_unpriced_calls(tmp_path, provider, tool, arg, args, limit):
    record = _call(_meter(tmp_path, provider), tool, {**args, arg: limit}, _text({}))
    request = record["request"]
    assert arg in request["argument_keys"]
    assert request["unrecognized_argument_key_count"] == 0
    assert request["pricing_arguments"][arg] == (
        limit if type(limit) is int and limit > 0 else "<unrecognized>"
    )
    assert "private-limit" not in json.dumps(record)
    assert record["response"]["meter"]["unpriced_reason"] == (
        "additional_result_count_unknown" if limit == 25 else "invalid_result_limit"
    )
    default = _call(_meter(tmp_path, provider), tool, args, _text({}))
    assert arg not in default["request"]["pricing_arguments"]


@pytest.mark.parametrize("disabled", ["web_search_exa", None, {}, [1]])
def test_disabled_tools_requires_a_list(disabled):
    with pytest.raises(ValueError, match="disabled_tools must be a list"):
        configured_pricing_tools("exa", {"disabled_tools": disabled})
    assert configured_pricing_tools("exa", {"disabled_tools": ["web_search_exa"]}) == [
        "web_fetch_exa"
    ]


@pytest.mark.parametrize(
    "registry", ["missing", "corrupt", "core-unavailable", "provider-absent", "provider-empty"]
)
def test_unavailable_tariffs_have_a_distinct_admission_error(tmp_path, monkeypatch, registry):
    from sew import mcp_meter

    if registry == "core-unavailable":
        monkeypatch.setattr(mcp_meter, "_core", lambda: None)
    elif registry not in ("provider-absent", "provider-empty"):
        path = tmp_path / "tariffs.yaml"
        if registry == "corrupt":
            path.write_text("providers: [")
        monkeypatch.setattr(mcp_meter, "default_tariffs_path", lambda: path)
    tariffs = (
        {"brave": {}}
        if registry == "provider-absent"
        else {"exa": {}}
        if registry == "provider-empty"
        else None
    )
    with pytest.raises(ValueError, match="pricing tariff registry unavailable for: exa"):
        require_pricing_parity({"exa": ["web_search_exa"]}, tariffs)


@pytest.mark.parametrize("count", ["²", "٢", "0", "-1", "9" * 5000])
def test_parallel_malformed_counts_do_not_crash_wrapper(tmp_path, count):
    config = {
        "command": "npx",
        "args": [
            "mcp-remote",
            "https://[broken",
            "https://search.parallel.ai/mcp-oauth?mode=fast&advanced_settings.max_results=" + count,
        ],
    }
    wrapped = metered_server_config(
        config, provider_id="parallel-web", run_id="r", call_dir=tmp_path
    )
    assert json.loads(wrapped["env"]["SEW_METER_PARALLEL_SEARCH_CONFIG"]) == {
        "mode": "fast",
        "max_results": "<unrecognized>",
    }


def test_exa_malformed_authority_does_not_hide_valid_selection():
    config = {"args": ["https://[broken", "https://mcp.exa.ai?tools=web_search_exa,new_tool"]}
    assert configured_pricing_tools("exa", config) == ["web_search_exa", "new_tool"]
    with pytest.raises(ValueError, match="pricing parity missing: exa:new_tool"):
        require_pricing_parity({"exa": configured_pricing_tools("exa", config)})


@pytest.mark.parametrize(
    "change,observed",
    [
        ({}, True),
        ({"schema_version": 2}, False),
        ({"provider_id": "exa"}, False),
        ({"run_id": "copied-run"}, False),
        ({"observed": False}, False),
    ],
)
def test_discovery_coverage_requires_observed_matching_identity(tmp_path, change, observed):
    _meter(tmp_path, "parallel-web")
    path = tmp_path / "provider-calls/availability.json"
    discovery = json.loads(path.read_text())
    discovery.update(pricing_missing_tools_n=3, **change)
    path.write_text(json.dumps(discovery))
    row = SimpleNamespace(provider_id="parallel-web", run_id="run-1", run_dir=tmp_path)
    coverage = pricing_coverage([row], load_price_table())["parallel-web"]
    assert coverage["missing_tool_rules_n"] == (3 if observed else 0)
    assert coverage["unobserved_cells_n"] == (0 if observed else 1)
