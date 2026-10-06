from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import MODULE_ROOT

from sew.arms import PROVIDER_SERVER_NAMES
from sew.bakeoff_report import (
    JSON_REPORT_NAME,
    MARKDOWN_REPORT_NAME,
    Control,
    build_bakeoff_report,
    newcombe_interval,
    render_bakeoff_markdown,
    _native_tools,
)
from sew.cli import main as cli_main
from sew.cost_model import parse_price_table
from sew.runner import SuiteRunner

FACT = "current-fact-lookup-v1"  # task_class fact_lookup
FRESH = "freshness-stale-trap-v1"  # task_class freshness
GENERATED_AT = "2026-09-26T00:00:00Z"
SECTIONS = (
    "# WSB Bakeoff Report:",
    "## Task completion by arm\n",
    "## Task completion by arm and task class",
    "## Deltas vs no_search (`<harness>+no-search`)",
    "## Deltas vs native (`<harness>+native`)",
    "## Cost per successful task",
    "## Marked cells",
    "## Warnings",
    "## Evidence index",
)
PRICES = parse_price_table(
    {
        "version": 1,
        "vendors": {},
        "models": {
            "claude-sonnet-5": {
                "input_usd_per_mtok": 2.0,
                "cached_input_usd_per_mtok": 0.2,
                "output_usd_per_mtok": 10.0,
                "rate_basis": "published_list_price",
                "source": "https://example.test/pricing",
                "as_of": "2026-09-01",
            }
        },
    }
)


def test_report_over_a_fixture_run_renders_every_section(tmp_path: Path) -> None:
    summary = SuiteRunner(state_root=tmp_path).run("lighthouse", run_id="fx", resume=False)
    run_root = Path(summary["run_root"])

    assert cli_main(["bakeoff", "report", str(run_root)]) == 0

    reports = run_root / "reports"
    markdown = (reports / MARKDOWN_REPORT_NAME).read_text(encoding="utf-8")
    report = json.loads((reports / JSON_REPORT_NAME).read_text(encoding="utf-8"))
    for section in SECTIONS:
        assert section in markdown, section
    for key in ("headline", "by_task_class", "deltas", "marked_cells", "warnings", "runs"):
        assert report[key], key
    assert "metering coverage" in markdown
    assert all(row["metering"]["observed_n"] == 0 for row in report["headline"])
    for row in report["headline"]:
        line = next(line for line in markdown.splitlines() if line.startswith(f"| {row['arm']} |"))
        expected = (
            f"0/{row['metering']['cells_n']}"
            if row["provider_id"] in PROVIDER_SERVER_NAMES
            else "n/a"
        )
        assert line.split("|")[4].strip() == expected
    assert report["mode"] == "fixture"
    assert report["run_counts"]["runs"] == summary["completed_cells"]
    assert {run["run_id"] for run in report["runs"]} == {
        entry["run_id"] for entry in json.loads((run_root / "run-index.json").read_text())
    }
    # The lighthouse budget stops the suite early; the report says so.
    assert any(warning["kind"] == "suite_incomplete" for warning in report["warnings"])
    # Every aggregate links to the evidence behind it, and the links resolve.
    for cell in [*report["by_task_class"], *report["marked_cells"]]:
        assert {item["run_id"] for item in cell["evidence"]} == set(cell["run_ids"])
        for item in cell["evidence"]:
            if item["bundle"] is not None:
                assert (reports / item["bundle"]).is_file()
    for run in report["runs"]:
        if run["disposition"] == "non_comparable":
            continue
        # Hosted harnesses keep a transcript; Pi keeps its harness record.
        trace = "harness_record" if run["arm"].startswith("pi+") else "transcript"
        for role in ("bundle", "usage", "evaluation", trace):
            assert (reports / run["evidence"][role]).is_file(), (run["run_id"], role)


def test_native_opus_report_prices_search_and_fetch_and_keeps_unknowns_unknown(
    tmp_path: Path,
) -> None:
    root = _run_root(tmp_path)
    entries = _arm(
        root,
        "claude-code",
        "native",
        ["pass"] * 3,
        usage={
            **_usage(1000, 1000, 100, 0),
            "cache_write": 600,
            "cache_write_5m": 400,
            "cache_write_1h": 200,
        },
        model_id="claude-opus-5-5[1m]",
        search_calls=2,
    )
    for entry in entries:
        run_dir = Path(entry["run_dir"])
        transcript = [
            {
                "harness_event": {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "id": "s1", "name": "WebSearch"},
                            {"type": "tool_use", "id": "f1", "name": "WebFetch"},
                        ]
                    },
                }
            },
        ]
        (run_dir / "artifacts" / "transcript.json").write_text(json.dumps(transcript))
    entries += _arm(
        root,
        "codex",
        "native",
        ["pass"] * 3,
        usage=_usage(1000, 0, 100, 0),
        model_id="gpt-5.5",
        search_calls=1,
    )
    _state(root, entries)
    report = build_bakeoff_report(root, module_base=MODULE_ROOT, generated_at=GENERATED_AT)
    priced = _headline(report, "claude-code+native")["cost"]
    assert priced["costed_n"] == 3
    assert priced["usd_per_success"] == pytest.approx(0.0174)
    assert priced["basis"] == "inferred"
    unknown = _headline(report, "codex+native")["cost"]
    assert unknown["usd_per_success"] is None
    assert any("search_calls_unpriced" in reason for reason in unknown["exclusion_reasons"])


def test_codex_native_search_is_priced_at_the_brave_proxy_rate(tmp_path: Path) -> None:
    """Operator decision 2026-09-30: codex's built-in search costs what Brave charges."""

    root = _run_root(tmp_path)
    entries = _arm(
        root,
        "codex",
        "native",
        ["pass"] * 3,
        usage=_usage(1000, 0, 500, 0),
        model_id="gpt-6-sol",
        search_calls=3,
    )
    for entry in entries:
        transcript = []
        for call_id, action in (("ws1", "search"), ("ws2", "open_page"), ("ws3", "other")):
            item = {"id": call_id, "type": "web_search", "action": {"type": action}}
            transcript += [
                {"harness_event": {"type": "item.started", "item": item}},
                {"harness_event": {"type": "item.completed", "item": item}},
            ]
        (Path(entry["run_dir"]) / "artifacts" / "transcript.json").write_text(
            json.dumps(transcript)
        )
    _state(root, entries)
    report = build_bakeoff_report(root, module_base=MODULE_ROOT, generated_at=GENERATED_AT)
    assert "every attempted run has a known cost" in render_bakeoff_markdown(report)
    cost = _headline(report, "codex+native")["cost"]
    assert cost["costed_n"] == 3
    # gpt-6-sol tokens $0.007, plus two searches ("other" counts as one) at $0.005;
    # the page open is free, and item.started does not double-count a call.
    assert cost["usd_per_success"] == pytest.approx(0.017)
    for run in report["runs"]:
        assert (run["cost_usd"], run["cost_exclusions"]) == (pytest.approx(0.017), [])
        assert run["cost_pricing_notes"] == ["proxy_rate:codex-native->brave"]
    assert "Operator proxy rates:\n- proxy_rate:codex-native->brave" in render_bakeoff_markdown(
        report
    )


def test_codex_native_search_above_provider_meter_is_unpriced(tmp_path: Path) -> None:
    root = _run_root(tmp_path)
    entries = _arm(
        root,
        "codex",
        "native",
        ["pass"],
        usage=_usage(1000, 0, 500, 0),
        model_id="gpt-6-sol",
        search_calls=1,
    )
    (Path(entries[0]["run_dir"]) / "artifacts" / "transcript.json").write_text(
        json.dumps(
            [
                {
                    "harness_event": {
                        "type": "item.completed",
                        "item": {
                            "type": "web_search",
                            "id": call_id,
                            "action": {"type": "search"},
                        },
                    }
                }
                for call_id in ("ws1", "ws2")
            ]
        )
    )
    _state(root, entries)
    report = build_bakeoff_report(root, module_base=MODULE_ROOT, generated_at=GENERATED_AT)
    run = report["runs"][0]
    assert run["cost_usd"] is None
    assert run["cost_exclusions"] == ["vendor:codex-native:search_meter_mismatch"]


def test_outcome_rate_and_tokens_are_always_reported_beside_each_other(tmp_path: Path) -> None:
    """An ungraded run withholds the strict rate, never the graded one or the tokens."""

    root = _run_root(tmp_path)
    entries = _arm(
        root,
        "claude-code",
        "exa",
        ["pass", "fail", "ungraded"],
        usage=_usage(600, 300, 80, 20),
    )
    _state(root, entries)
    report = build_bakeoff_report(
        root, module_base=MODULE_ROOT, generated_at=GENERATED_AT, price_table=PRICES
    )
    row = _headline(report, "claude-code+exa")
    assert row["success_rate"] is None and row["rate_withheld"] == "ungraded"
    assert (row["graded_successes"], row["graded_n"], row["ungraded_n"]) == (1, 2, 1)
    assert row["graded_success_rate"] == pytest.approx(0.5)
    tokens = row["tokens"]
    assert tokens["per_task"] == pytest.approx(1000)
    # Two graded runs, 2,000 tokens, one success; the ungraded run is in neither.
    assert (tokens["per_success"], tokens["per_success_n"]) == (pytest.approx(2000), 2)
    assert tokens["per_success_successes_n"] == 1
    assert tokens["mix"] == {
        "input": pytest.approx(600),
        "cached_input": pytest.approx(300),
        "output": pytest.approx(100),
    }
    assert sum(tokens["mix"].values()) == pytest.approx(tokens["per_task"])

    table = _section(render_bakeoff_markdown(report), "## Task completion by arm\n")
    assert "withheld (ungraded); graded 50.0% (1/2, 1 ungraded)" in table
    assert "| 2,000 (1 measured successes / 2 measured of 2 graded) |" in table
    assert "600 / 300 / 100 (3/3)" in table


def test_token_mix_excludes_inconsistent_and_boolean_buckets(tmp_path: Path) -> None:
    root = _run_root(tmp_path)
    inconsistent = _usage(600, 300, 80, 20)
    inconsistent["total_billable"] = 1100
    entries = _arm(root, "claude-code", "exa", ["pass"], usage=inconsistent)
    boolean_bucket = _usage(600, 300, 80, 20)
    boolean_bucket["reasoning"] = True
    entries += _arm(root, "claude-code", "exa", ["fail"], first_rep=2, usage=boolean_bucket)
    _state(root, entries)
    report = build_bakeoff_report(root, module_base=MODULE_ROOT, generated_at=GENERATED_AT)
    tokens = _headline(report, "claude-code+exa")["tokens"]
    assert tokens["mix"] is None
    assert tokens["mix_n"] == 0


def test_codex_spawn_model_flows_into_report_price_and_missing_model_stays_unknown(
    tmp_path: Path,
) -> None:
    root = _run_root(tmp_path)
    entries = _arm(
        root,
        "codex",
        "no-search",
        ["pass"],
        usage=_usage(1000, 0, 500, 0),
        model_id="gpt-6-sol",
    )
    entries += _arm(
        root,
        "codex",
        "no-search",
        ["pass"],
        first_rep=2,
        usage=_usage(1000, 0, 500, 0),
    )
    _state(root, entries)
    report = build_bakeoff_report(root, module_base=MODULE_ROOT, generated_at=GENERATED_AT)
    runs = {run["repetition"]: run for run in report["runs"]}
    assert runs[1]["model_id_source"] == "spawn_config"
    assert runs[1]["cost_usd"] == pytest.approx(0.007)
    assert runs[2]["cost_usd"] is None
    assert runs[2]["cost_exclusions"] == ["model:unknown-model:model_id_unknown"]


def test_legacy_opus_bundle_exposes_cache_write_lower_bound(tmp_path: Path) -> None:
    root = _run_root(tmp_path)
    usage = _usage(1000, 1000, 100, 0)
    del usage["cache_write"]
    entries = _arm(
        root,
        "claude-code",
        "no-search",
        ["pass"] * 3,
        usage=usage,
        model_id="claude-opus-5-5",
    )
    _state(root, entries)
    report = build_bakeoff_report(root, module_base=MODULE_ROOT, generated_at=GENERATED_AT)
    assert _headline(report, "claude-code+no-search")["cost"]["basis"] == "estimated"
    for run in report["runs"]:
        assert run["cost_basis"] == "estimated"
        assert run["cost_pricing_notes"] == ["cache_writes_priced_at_base_input"]


@pytest.mark.parametrize(
    "response", ["broken", ["broken"], {"meter": "broken"}, {"meter": ["broken"]}]
)
@pytest.mark.parametrize("provider", ["exa", "brave"])
def test_malformed_call_response_does_not_abort_report(tmp_path: Path, response, provider) -> None:
    root = _run_root(tmp_path)
    entries = _arm(
        root,
        "claude-code",
        provider,
        ["pass"],
        usage=_usage(100, 0, 10, 0),
        model_id="claude-sonnet-5",
        vendor_usd=0.01,
    )
    _state(root, entries)
    path = root / "bundles" / entries[0]["run_id"] / "provider-calls/search-1.json"
    record = json.loads(path.read_text())
    record["response"] = response
    if provider == "brave":
        record.update(operation="brave_web_search", status="failed")
        if isinstance(response, dict):
            record["response"] = {**response, "error_kind": "empty_result"}
    _write(path, record)
    report = build_bakeoff_report(root, module_base=MODULE_ROOT, generated_at=GENERATED_AT)
    assert report["runs"][0]["cost_usd"] is None
    assert report["runs"][0]["cost_exclusions"]
    assert report["pricing_coverage"][provider]["unpriced_n"] == 1


def test_failed_native_search_is_counted_but_has_no_tool_fee(tmp_path: Path) -> None:
    run_dir = tmp_path
    (run_dir / "artifacts").mkdir()
    (run_dir / "artifacts" / "transcript.json").write_text(
        json.dumps(
            [
                {
                    "harness_event": {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {"type": "tool_use", "id": "s1", "name": "WebSearch"},
                            ]
                        },
                    }
                },
                {
                    "harness_event": {
                        "type": "user",
                        "message": {
                            "content": [
                                {"type": "tool_result", "tool_use_id": "s1", "is_error": True},
                            ]
                        },
                    }
                },
            ]
        )
    )
    assert _native_tools(run_dir) == [("WebSearch", True)]


def test_unknown_token_cells_are_excluded_from_cost_tables_with_a_visible_warning(
    tmp_path: Path,
) -> None:
    root = _run_root(tmp_path)
    entries = _arm(
        root,
        "claude-code",
        "exa",
        ["pass", "pass", "fail"],
        usage=_usage(1000, 0, 100, 0),
        model_id="claude-sonnet-5",
        vendor_usd=0.01,
    )
    entries += _arm(root, "claude-code", "exa", ["pass"], usage=None, first_rep=4)
    entries += _arm(root, "claude-code", "firecrawl", ["pass"] * 3, usage=None)
    _state(root, entries)

    report = build_bakeoff_report(
        root, module_base=MODULE_ROOT, generated_at=GENERATED_AT, price_table=PRICES
    )
    exa = _headline(report, "claude-code+exa")
    # Tokens do not decide the rate: the unknown-token run still counts.
    assert (exa["n"], exa["successes"]) == (4, 3)
    assert exa["tokens"]["per_task"] == 1100
    assert (exa["tokens"]["measured_n"], exa["tokens"]["unknown_n"]) == (3, 1)
    cost = exa["cost"]
    per_run = (1000 * 2.0 + 100 * 10.0) / 1_000_000 + 0.01
    assert (cost["costed_n"], cost["excluded_n"], cost["successes_costed"]) == (3, 1, 2)
    assert cost["usd_per_task"] == pytest.approx(per_run)
    assert cost["usd_per_success"] == pytest.approx(3 * per_run / 2)
    assert cost["exclusion_reasons"] == ["tokens:unknown"]
    firecrawl = _headline(report, "claude-code+firecrawl")
    assert firecrawl["cost"]["in_cost_table"] is False
    assert firecrawl["cost"]["usd_per_task"] is None

    markdown = render_bakeoff_markdown(report)
    assert "pricing_coverage" in report
    assert "## Vendor pricing coverage" in markdown
    cost_section = _section(markdown, "## Cost per successful task")
    assert "| claude-code+exa | 3/4 | 2 | $0.0130 | $0.0195 | inferred |" in cost_section
    assert "| claude-code+firecrawl |" not in cost_section
    assert "$0.0000" not in markdown
    assert (
        "⚠ claude-code+exa / fact_lookup: 1 run of 4 attempted has unknown token usage; "
        "excluded from token and cost tables, not counted as zero"
    ) in cost_section
    assert "⚠ claude-code+firecrawl: excluded from the cost table, never shown as $0" in (
        cost_section
    )
    assert "unmeasured (0/3)" in _section(markdown, "## Task completion by arm\n")


def test_non_comparable_contaminated_and_degraded_cells_are_marked_not_dropped(
    tmp_path: Path,
) -> None:
    root = _run_root(tmp_path)
    entries = _arm(root, "claude-code", "no-search", ["fail"] * 3, usage=_usage(900, 0, 100, 0))
    entries += _arm(root, "claude-code", "exa", ["pass"] * 3, usage=_usage(400, 0, 100, 0))
    # Succeeded on paper, but its transcript reached a tool outside the arm.
    entries += _arm(root, "claude-code", "exa", ["pass"], first_rep=4, contaminated=True)
    entries += _arm(root, "claude-code", "firecrawl", ["pass"] * 3)
    entries += _arm(root, "claude-code", "firecrawl", ["provider_unavailable"] * 2, first_rep=4)
    entries += _arm(
        root,
        "claude-code",
        "brave",
        ["ungraded"] * 3,
        usage=_usage(300, 0, 50, 0),
        model_id="claude-sonnet-5",
    )
    entries += _arm(root, "claude-code", "tavily", ["pass"])
    entries += [
        _not_applicable(root, "pi", "native", "oss-large", rep, "native_search_unavailable")
        for rep in (1, 2, 3)
    ]
    _state(root, entries)

    report = build_bakeoff_report(
        root, module_base=MODULE_ROOT, generated_at=GENERATED_AT, price_table=PRICES
    )
    cells = {cell["arm"]: cell for cell in report["by_task_class"]}
    assert set(cells) == {
        "claude-code+no-search",
        "claude-code+exa",
        "claude-code+firecrawl",
        "claude-code+brave",
        "claude-code+tavily",
        "pi+native [oss-large]",
    }
    pi = cells["pi+native [oss-large]"]
    assert (pi["runs"], pi["n"], pi["success_rate"]) == (3, 0, None)
    assert pi["marks"] == {"non_comparable": 3}
    assert pi["rate_withheld"] == "no_attempted_runs"
    exa = cells["claude-code+exa"]
    assert (exa["runs"], exa["n"], exa["success_rate"]) == (4, 3, 1.0)
    assert exa["marks"]["contaminated"] == 1
    firecrawl = cells["claude-code+firecrawl"]
    assert (firecrawl["n"], firecrawl["marks"]["provider_unavailable"]) == (3, 2)
    brave = cells["claude-code+brave"]
    assert (brave["success_rate"], brave["rate_withheld"]) == (None, "ungraded")
    # Priced, but ungraded runs are not successes: $/success would be inflated.
    assert brave["cost"]["costed_n"] == 3
    assert brave["cost"]["usd_per_success_status"] == "withheld_ungraded"
    assert cells["claude-code+tavily"]["marks"]["under_sampled"] == 1

    marked = {(item["mark"], item["arm"]) for item in report["marked_cells"]}
    contaminated = next(item for item in report["marked_cells"] if item["mark"] == "contaminated")
    # A mark links the runs it applies to, not the whole cell.
    assert contaminated["run_ids"] == ["claude-code-exa-current-fact-lookup-v1-4"]
    assert [item["run_id"] for item in contaminated["evidence"]] == contaminated["run_ids"]
    assert {
        ("non_comparable", "pi+native [oss-large]"),
        ("contaminated", "claude-code+exa"),
        ("provider_unavailable", "claude-code+firecrawl"),
        ("ungraded", "claude-code+brave"),
        ("under_sampled", "claude-code+tavily"),
    } <= marked
    deltas = _deltas(report, "no_search")
    assert deltas[("pi+native [oss-large]", "fact_lookup")]["marks"] == [
        "subject_no_attempted_runs",
        "control_missing",
    ]
    assert deltas[("claude-code+tavily", "fact_lookup")]["marks"] == ["subject_under_sampled"]
    brave_delta = deltas[("claude-code+brave", "fact_lookup")]
    assert brave_delta["success_delta"] is None
    assert "subject_rate_withheld:ungraded" in brave_delta["marks"]
    assert brave_delta["token_ratio"] == pytest.approx(350 / 1000)
    assert _headline(report, "claude-code+tavily")["excluded_task_classes"] == {
        "fact_lookup": ["under_sampled"]
    }

    markdown = render_bakeoff_markdown(report)
    kinds = {warning["kind"] for warning in report["warnings"]}
    assert {"non_comparable", "contaminated", "provider_unavailable", "ungraded"} <= kinds
    assert "| non_comparable | pi+native [oss-large] | fact_lookup | 3 | 0 |" in markdown
    assert "(no bundle)" in markdown
    assert "withheld (ungraded)" in markdown
    assert "- `contaminated`: transcript shows a tool call outside the arm" in markdown


def test_deltas_are_computed_against_the_declared_control_arms(tmp_path: Path) -> None:
    root = _run_root(tmp_path)
    entries = []
    for harness, provider, outcomes, tokens in (
        ("claude-code", "exa", ["pass"] * 3, 1000),
        ("claude-code", "no-search", ["fail", "fail", "pass"], 4000),
        ("claude-code", "native", ["pass", "fail", "fail"], 2000),
        # A cross-harness comparison would read codex+exa against the
        # claude-code controls; the codex control inverts every sign.
        ("codex", "exa", ["fail"] * 3, 500),
        ("codex", "no-search", ["pass"] * 3, 250),
    ):
        entries += _arm(root, harness, provider, outcomes, usage=_usage(tokens - 100, 0, 100, 0))
    entries += _arm(root, "claude-code", "exa", ["pass"] * 3, task_id=FRESH, first_rep=11)
    entries += _arm(root, "claude-code", "no-search", ["fail"], task_id=FRESH, first_rep=11)
    _state(root, entries)

    report = build_bakeoff_report(
        root, module_base=MODULE_ROOT, generated_at=GENERATED_AT, price_table=PRICES
    )
    assert [control["provider_id"] for control in report["controls"]] == ["no-search", "native"]
    no_search = _deltas(report, "no_search")
    native = _deltas(report, "native")

    row = no_search[("claude-code+exa", "fact_lookup")]
    assert row["control_arm"] == "claude-code+no-search"
    assert row["success_delta"] == pytest.approx(2 / 3)
    assert row["success_delta_ci_95"] == newcombe_interval(3, 3, 1, 3)
    assert row["token_ratio"] == pytest.approx(0.25)
    assert row["token_delta_per_task"] == pytest.approx(-3000)
    # 1,000 tokens per exa success against 12,000 per no-search success.
    assert row["tokens_per_success_ratio"] == pytest.approx(1000 / 12000)
    assert row["token_denominator"] == (
        "measured tokens per attempted task: claude-code+exa 1,000 over 3 of 3 runs / "
        "claude-code+no-search 4,000 over 3 of 3 runs"
    )
    assert native[("claude-code+exa", "fact_lookup")]["success_delta"] == pytest.approx(2 / 3)
    assert native[("claude-code+exa", "fact_lookup")]["token_ratio"] == pytest.approx(0.5)
    # Native is compared with the control declared before it, never mirrored.
    native_vs_none = no_search[("claude-code+native", "fact_lookup")]
    assert native_vs_none["success_delta"] == pytest.approx(0.0)
    assert native_vs_none["token_ratio"] == pytest.approx(0.5)
    assert not any(arm.endswith("+no-search") for arm, _ in native)
    assert not any(arm.endswith("+native") for arm, _ in native)

    codex = no_search[("codex+exa", "fact_lookup")]
    assert codex["control_arm"] == "codex+no-search"
    assert (codex["success_delta"], codex["token_ratio"]) == (pytest.approx(-1.0), 2.0)
    assert native[("codex+exa", "fact_lookup")]["marks"] == ["control_missing"]
    assert native[("codex+exa", "fact_lookup")]["success_delta"] is None

    thin = no_search[("claude-code+exa", "freshness")]
    assert (thin["success_delta"], thin["token_ratio"]) == (None, None)
    assert thin["marks"] == ["control_under_sampled"]
    pooled = no_search[("claude-code+exa", "all")]
    assert pooled["task_classes"] == ["fact_lookup"]
    assert pooled["subject"]["n"] == 3
    assert pooled["success_delta"] == pytest.approx(2 / 3)

    markdown = render_bakeoff_markdown(report)
    table = _section(markdown, "## Deltas vs no_search")
    assert "| claude-code | fact_lookup | claude-code+exa | 100.0% (n=3) | 33.3% (n=3) |" in table
    assert "0.25x (4.0x fewer)" in table
    assert "| codex | fact_lookup | codex+exa |" in table and "2.00x (2.0x more)" in table

    declared = build_bakeoff_report(
        root,
        module_base=MODULE_ROOT,
        generated_at=GENERATED_AT,
        price_table=PRICES,
        controls=(Control("exa_baseline", "exa"),),
    )
    custom = _deltas(declared, "exa_baseline")
    assert custom[("claude-code+no-search", "fact_lookup")]["success_delta"] == pytest.approx(
        -2 / 3
    )
    assert custom[("codex+no-search", "fact_lookup")]["token_ratio"] == pytest.approx(0.5)
    assert "## Deltas vs exa_baseline (`<harness>+exa`)" in render_bakeoff_markdown(declared)


def test_live_usage_is_priced_from_disjoint_buckets_and_unpriced_search_is_unknown(
    tmp_path: Path,
) -> None:
    root = _run_root(tmp_path)
    # Live driver buckets: reasoning moved out of output, cache reads out of input.
    entries = _arm(
        root,
        "claude-code",
        "no-search",
        ["pass"] * 3,
        usage=_usage(1000, 500, 200, 100),
        transcript_model="claude-sonnet-5",
    )
    entries += _arm(
        root,
        "claude-code",
        "exa",
        ["pass"] * 3,
        usage=_usage(1000, 0, 200, 0),
        model_id="claude-sonnet-5",
        search_calls=2,
    )
    bad = _usage(1000, 0, 200, 100)
    bad["total_billable"] = 1200
    entries += _arm(
        root, "claude-code", "native", ["pass"] * 3, usage=bad, model_id="claude-sonnet-5"
    )
    _state(root, entries)

    report = build_bakeoff_report(
        root, module_base=MODULE_ROOT, generated_at=GENERATED_AT, price_table=PRICES
    )
    runs = {run["arm"]: run for run in report["runs"]}
    control = runs["claude-code+no-search"]
    assert control["model_id_source"] == "harness_transcript"
    assert control["cost_usd"] == pytest.approx((1000 * 2.0 + 500 * 0.2 + 300 * 10.0) / 1e6)
    assert runs["claude-code+exa"]["cost_usd"] is None
    assert runs["claude-code+exa"]["cost_exclusions"] == ["vendor:exa:2_search_calls_unpriced"]
    assert runs["claude-code+native"]["cost_exclusions"] == ["tokens:buckets_not_disjoint"]
    exa = _headline(report, "claude-code+exa")
    assert exa["cost"]["in_cost_table"] is False
    # Measured tokens with unpriced spend is an unknown cost, and is marked as one.
    assert exa["marks"]["unknown_cost"] == 3
    assert _headline(report, "claude-code+native")["marks"]["unknown_cost"] == 3
    assert _headline(report, "claude-code+no-search")["cost"]["usd_per_success"] == (
        pytest.approx(0.0051)
    )
    assert report["price_sources"] == [
        {"price_source": "https://example.test/pricing", "price_as_of": "2026-09-01"}
    ]


def test_task_class_falls_back_to_the_production_catalog(tmp_path: Path) -> None:
    root = _run_root(tmp_path)
    entries = _arm(
        root, "claude-code", "exa", ["pass"] * 3, task_id="list-build-python313-pep594-removals"
    )
    _state(root, entries)

    report = build_bakeoff_report(
        root, module_base=MODULE_ROOT, generated_at=GENERATED_AT, price_table=PRICES
    )
    assert [cell["task_class"] for cell in report["by_task_class"]] == ["list_build"]


def test_newcombe_interval_matches_the_published_reference() -> None:
    # Newcombe (1998), Statistics in Medicine 17:873, method 10, 56/70 vs 48/80.
    interval = newcombe_interval(56, 70, 48, 80)
    assert interval["low"] == pytest.approx(0.0524, abs=1e-4)
    assert interval["high"] == pytest.approx(0.3339, abs=1e-4)
    assert newcombe_interval(1, 0, 1, 3) is None


# --------------------------------------------------------------------------
# Hand-built suite run roots
# --------------------------------------------------------------------------


def _run_root(tmp_path: Path) -> Path:
    root = tmp_path / ".sew" / "runs" / "wsb-fixture"
    (root / "bundles").mkdir(parents=True)
    return root


def _state(root: Path, index: list[dict[str, Any]]) -> None:
    state = {
        "schema_version": 1,
        "suite_id": "production",
        "suite_version": "1",
        "seed": 7,
        "mode": "live",
        "order": [entry["cell_key"] for entry in index],
        "index": index,
        "summary": {"completed_cells": len(index), "remaining_cells": 0},
    }
    (root / "runner-state.json").write_text(json.dumps(state), encoding="utf-8")
    (root / "run-index.json").write_text(json.dumps(index), encoding="utf-8")


def _usage(input_: int, cached: int, output: int, reasoning: int) -> dict[str, Any]:
    return {
        "accounting_source": "measured",
        "source_kind": "harness_usage_rows",
        "input": input_,
        "cache_write": 0,
        "cached_input": cached,
        "output": output,
        "reasoning": reasoning,
        "total_billable": input_ + cached + output + reasoning,
    }


def _arm(
    root: Path,
    harness: str,
    provider: str,
    outcomes: list[str],
    *,
    usage: dict[str, Any] | None = None,
    task_id: str = FACT,
    first_rep: int = 1,
    **bundle: Any,
) -> list[dict[str, Any]]:
    """One bundle per outcome: pass, fail, ungraded, or a terminal run status."""

    entries = []
    for offset, outcome in enumerate(outcomes):
        repetition = first_rep + offset
        run_id = f"{harness}-{provider}-{task_id}-{repetition}"
        status = "succeeded" if outcome in {"pass", "fail", "ungraded"} else outcome
        _bundle(root, run_id, harness, provider, task_id, status, outcome, usage, **bundle)
        entries.append(
            {
                "cell_key": f"{task_id}|{provider}|{harness}|default|{repetition}",
                "run_id": run_id,
                "task_id": task_id,
                "provider_id": provider,
                "harness_id": harness,
                "model_profile": "default",
                "repetition": repetition,
                "status": status,
                "terminal": True,
                "attempts": 1,
                "run_dir": str(root / "bundles" / run_id),
            }
        )
    return entries


def _not_applicable(
    root: Path, harness: str, provider: str, profile: str, repetition: int, reason: str
) -> dict[str, Any]:
    return {
        "cell_key": f"{FACT}|{provider}|{harness}|{profile}|{repetition}",
        "run_id": f"{harness}-{provider}-{profile}-{repetition}",
        "task_id": FACT,
        "provider_id": provider,
        "harness_id": harness,
        "model_profile": profile,
        "repetition": repetition,
        "status": "not_applicable",
        "terminal": True,
        "attempts": 0,
        "not_applicable_reason": reason,
    }


def _bundle(
    root: Path,
    run_id: str,
    harness: str,
    provider: str,
    task_id: str,
    status: str,
    outcome: str,
    usage: dict[str, Any] | None,
    *,
    model_id: str | None = None,
    transcript_model: str | None = None,
    search_calls: int = 0,
    vendor_usd: float | None = None,
    contaminated: bool = False,
) -> None:
    run_dir = root / "bundles" / run_id
    for rel in ("metrics", "evaluations", "evidence", "artifacts", "provider-calls"):
        (run_dir / rel).mkdir(parents=True, exist_ok=True)
    call_refs = []
    if vendor_usd is not None:
        call_refs = ["provider-calls/search-1.json"]
        _write(
            run_dir / call_refs[0],
            {
                "schema_version": 1,
                "call_id": f"{run_id}-search-1",
                "run_id": run_id,
                "provider_id": provider,
                "operation": "search",
                "status": "ok",
                "started_at": "2026-09-26T00:00:01Z",
                "ended_at": "2026-09-26T00:00:02Z",
                "request": {"operation": "search", "redacted": True},
                "response": {"provider_cost": {"currency": "USD", "total": vendor_usd}},
                "normalized_source_refs": [],
                "retry_count": 0,
            },
        )
    _write(
        run_dir / "run.json",
        {
            "schema_version": 1,
            "run_id": run_id,
            "suite_id": "production",
            "task_id": task_id,
            "provider_id": provider,
            "harness_id": harness,
            "model_profile": "default",
            "mode": "live",
            "status": status,
            "started_at": "2026-09-26T00:00:00Z",
            "ended_at": "2026-09-26T00:01:00Z",
            "metrics_ref": "metrics/metrics.json",
            "evaluation_ref": "evaluations/evaluation.json",
            "evidence_bundle_ref": "evidence/bundle.yaml",
            "provider_call_refs": call_refs,
        },
    )
    token_usage = usage or {
        "accounting_source": "unknown",
        "source_kind": "unknown",
        "input": None,
        "cached_input": None,
        "output": None,
        "reasoning": None,
        "total_billable": None,
    }
    _write(
        run_dir / "metrics" / "metrics.json",
        {
            "schema_version": 1,
            "run_id": run_id,
            "latency_ms": {"total_wall": 60000, "end_to_end": 60000},
            "token_usage": token_usage,
            "provider_calls": {"total": len(call_refs)},
            "failure_categories": [],
        },
    )
    verdict = {"pass": "pass", "fail": "fail"}.get(outcome, "not_applicable")
    _write(
        run_dir / "evaluations" / "evaluation.json",
        {
            "schema_version": 1,
            "run_id": run_id,
            "task_id": task_id,
            "outcome": verdict,
            "dimensions": {"completed": status == "succeeded", "correct": verdict == "pass"},
            "failure_reasons": [] if verdict == "pass" else [f"{outcome}"],
            "judge": {"kind": "fixture"},
            "evidence_refs": [],
        },
    )
    transcript: list[dict[str, Any]] = [{"role": "user", "content": "task"}]
    if transcript_model:
        transcript.append(
            {
                "role": "harness",
                "received_at": "2026-09-26T00:00:01Z",
                "harness_event": {"type": "system", "subtype": "init", "model": transcript_model},
            }
        )
    (run_dir / "artifacts" / "transcript.json").write_text(json.dumps(transcript))
    _write(
        run_dir / "artifacts" / "spawn-metadata.json",
        {
            "harness_id": harness,
            "model_id": model_id,
            "arm_audit": {"contaminated": contaminated, "violations": []},
            "process": {"provider_calls": search_calls},
        },
    )
    (run_dir / "evidence" / "bundle.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "bundle_id": f"{run_id}-bundle",
                "run_id": run_id,
                "redaction": {
                    "raw_transcripts_included": False,
                    "credentials_included": False,
                    "cookies_included": False,
                    "unrestricted_page_archives_included": False,
                },
                "artifacts": [
                    {"path": "artifacts/transcript.json", "media_type": "application/json"},
                    {"path": "artifacts/spawn-metadata.json", "media_type": "application/json"},
                ],
                "records": {
                    "run": "run.json",
                    "metrics": "metrics/metrics.json",
                    "evaluation": "evaluations/evaluation.json",
                    "provider_calls": call_refs,
                    "normalized_sources": [],
                },
            }
        ),
        encoding="utf-8",
    )


def _write(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _headline(report: dict[str, Any], arm: str) -> dict[str, Any]:
    return next(row for row in report["headline"] if row["arm"] == arm)


def _deltas(report: dict[str, Any], control: str) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (row["arm"], row["task_class"]): row
        for row in report["deltas"]
        if row["control"] == control
    }


def _section(markdown: str, heading: str) -> str:
    start = markdown.index(heading)
    end = markdown.find("\n## ", start + len(heading))
    return markdown[start : end if end != -1 else None]


def test_interrupted_codex_search_has_distinct_cost_reason(tmp_path: Path) -> None:
    root = _run_root(tmp_path)
    entries = _arm(
        root,
        "codex",
        "native",
        ["pass"],
        usage=_usage(1000, 0, 500, 0),
        model_id="gpt-6-sol",
        search_calls=1,
    )
    (Path(entries[0]["run_dir"]) / "artifacts" / "transcript.json").write_text(
        json.dumps(
            [
                {
                    "harness_event": {
                        "type": "item.started",
                        "item": {"type": "web_search", "id": "unfinished"},
                    }
                }
            ]
        )
    )
    _state(root, entries)
    report = build_bakeoff_report(root, module_base=MODULE_ROOT, generated_at=GENERATED_AT)
    assert report["runs"][0]["cost_usd"] is None
    assert "codex-native:uncompleted_search_items" in report["runs"][0]["cost_exclusions"]
