"""Offline acceptance fixtures for task-paired job reports."""

import json
import math
from pathlib import Path
from itertools import product

import pytest

from conftest import MODULE_ROOT
from test_bakeoff_report import _bundle, _usage, _write, PRICES
from sew.cli import main
from sew.gap.report import (
    build_gap_report,
    exact_mcnemar,
    paired_bootstrap,
    explain_gap,
    render_gap_markdown,
)
from sew.report import ReportError


def test_published_exact_mcnemar():
    # statsmodels 0.15.0 reference values (11:1 and 25:15 discordances):
    # https://www.statsmodels.org/stable/generated/statsmodels.stats.contingency_tables.mcnemar.html
    assert exact_mcnemar(11, 1) == pytest.approx(0.00634765625)
    assert exact_mcnemar(25, 15) == pytest.approx(0.153859944162832)
    assert exact_mcnemar(1, 11) == exact_mcnemar(11, 1)
    assert exact_mcnemar(0, 0) == 1
    assert exact_mcnemar(20, 20) == 1


def test_exact_mcnemar_large_discordance_counts():
    # The exact two-integer ratio remains safe above the float exponent limit.
    # With one loss, the doubled binomial tail is 2 * (1 + n) / 2**n.
    expected = math.ldexp(1025.0, -1023)
    assert exact_mcnemar(1023, 1) == expected
    assert exact_mcnemar(1, 1023) == expected
    for half in (512, 1024, 5000):
        assert exact_mcnemar(half, half) == 1
    assert exact_mcnemar(600, 424) == 2 * sum(math.comb(1024, k) for k in range(425)) / 2**1024
    assert exact_mcnemar(2048, 0) == 0  # A tail below float precision underflows safely.


def test_bootstrap_percentile_reference():
    # SciPy's documented paired percentile procedure, independently enumerated:
    # https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html
    # Constant floor=0, ceiling=1 turns closure into a sample mean.
    values = [0, 0.25, 0.5, 0.75, 1]
    distribution = sorted(sum(x) / 5 for x in product(values, repeat=5))
    reference = (
        distribution[int(0.025 * len(distribution))],
        distribution[int(0.975 * len(distribution))],
    )
    result = paired_bootstrap([(a, 0, 1) for a in values], seed=42)
    assert result["value"] == 0.5
    # SciPy 1.18.1 bootstrap(... rng=np.random.default_rng(42), paired=True,
    # method='percentile', n_resamples=10_000) gives (0.2, 0.8).
    assert result["ci_95"] == {"low": 0.2, "high": 0.8}
    assert result["ci_95"]["low"] == pytest.approx(reference[0], abs=0.05)
    assert result["ci_95"]["high"] == pytest.approx(reference[1], abs=0.05)
    assert result["resamples"] == 10_000
    assert result == paired_bootstrap([(a, 0, 1) for a in values], seed=42)
    assert paired_bootstrap([(0.6, 0.2, 1)] * 3)["value"] == pytest.approx(0.5)
    assert paired_bootstrap([(0, 1, 0)])["reason"] == "nonpositive_reference_gap"
    undefined = paired_bootstrap([(1, 0, 1), (0, 0, 0)])
    assert undefined["undefined_resamples_n"] > 0
    assert undefined["ci_95"] is None
    assert paired_bootstrap([])["reason"] == "no_paired_tasks"


@pytest.fixture
def suite(tmp_path):
    root = tmp_path / "suite"
    root.mkdir()
    tasks = [
        dict(task_id=t, kind=k, family=f, verdict="admitted", reason="admitted", date="2026-09-30")
        for t, k, f in [
            ("gap-a", "gap", "api-break"),
            ("gap-b", "gap", "decision-brief"),
            ("control", "control", "control"),
        ]
    ]
    tasks += [
        dict(
            task_id="retired", kind="gap", family="api-break", verdict="retired", reason="decayed"
        ),
        dict(
            task_id="rejected", kind="gap", family="api-break", verdict="rejected", reason="no gap"
        ),
    ]
    calibration = dict(harness="claude-code", model="claude-sonnet-5", tasks=tasks)
    entries = []
    for task in ("gap-a", "gap-b", "control", "retired"):
        for arm in ("floor", "ceiling", "native", "brave"):
            for rep in (1, 2):
                passed = (
                    arm == "ceiling"
                    or (task == "control" and arm != "brave")
                    or (arm in ("native", "brave") and rep == 1)
                )
                run_id = f"{task}-{arm}-{rep}"
                _bundle(
                    root,
                    run_id,
                    "claude-code",
                    arm,
                    task,
                    "succeeded",
                    "pass" if passed else "fail",
                    _usage(100 + (50 if arm == "brave" else 0), 200, 30, 10),
                    model_id="claude-sonnet-5",
                    search_calls=2 if arm in ("native", "brave") else 0,
                )
                path = root / "bundles" / run_id
                evaluation = {"outcome": "pass" if passed else "fail"}
                if task != "gap-b":
                    evaluation.update(
                        escaped_defects=0 if passed else 2,
                        visible_regressions=0 if passed else 1,
                        hidden=[
                            {
                                "tests": []
                                if passed
                                else [{"id": "test_timeout", "status": "failure"}]
                            }
                        ],
                        visible=[{"tests": [{"id": "visible", "status": "pass"}]}],
                    )
                else:
                    evaluation.update(
                        key_fact_recall=0.8,
                        unsupported_claim_rate=0.1,
                        agreement={"cohens_kappa": 0.5},
                    )
                _write(path / "evaluations/gap-outcome.json", evaluation)
                entries.append(
                    dict(
                        run_id=run_id,
                        run_dir=str(path),
                        task_id=task,
                        provider_id=arm,
                        harness_id="claude-code",
                        model_profile="default",
                        repetition=rep,
                        status="succeeded",
                    )
                )
    _write(root / "runner-state.json", dict(calibration=calibration))
    (root / "run-index.json").write_text(json.dumps(entries))
    return root


def report(root):
    return build_gap_report(root, module_base=MODULE_ROOT, price_table=PRICES)


def headline(r, arm):
    return next(x for x in r["headline"] if x["arm"] == arm)


def test_availability_coverage_and_unavailable_excluded_from_rates(suite):
    from sew.mcp_meter import CallMeter

    index = json.loads((suite / "run-index.json").read_text())
    for entry in index:
        if entry["provider_id"] != "brave":
            continue
        directory = Path(entry["run_dir"])
        meter = CallMeter(
            provider_id="brave", run_id=entry["run_id"], call_dir=directory / "provider-calls"
        )
        if entry["repetition"] == 2:
            entry["status"] = "provider_unavailable"
            run = json.loads((directory / "run.json").read_text())
            run["status"] = "provider_unavailable"
            _write(directory / "run.json", run)
            (directory / "evaluations/gap-outcome.json").unlink()
        else:
            for id, method, result in [
                (1, "initialize", {"protocolVersion": "2025-06-18"}),
                (2, "tools/list", {"tools": [{"name": "search"}]}),
            ]:
                meter.client_message({"id": id, "method": method})
                meter.server_message({"id": id, "result": result})
    _write(suite / "run-index.json", index)
    r = report(suite)
    brave = headline(r, "brave")
    assert brave["pass_rate"] == 1  # Two unavailable failures are not denominator cells.
    assert brave["availability"] == dict(
        available_n=2, unavailable_n=2, unknown_n=0, cells_n=4, coverage=0.5
    )
    assert (
        sum(brave["availability"][k] for k in ("available_n", "unavailable_n", "unknown_n"))
        == brave["cells_n"]
    )
    assert all(
        c["provider_available"] is False
        for c in r["cells"]
        if c["arm"] == "brave" and c["status"] == "provider_unavailable"
    )
    assert brave["gap_closure"]["pairs_n"] == 2
    assert brave["metering"]["observed_n"] == 4
    markdown = render_gap_markdown(r)
    assert "metering coverage" in markdown
    assert "| native | n/a | n/a |" in markdown
    assert "| brave | 2/4 | 4/4 |" in markdown
    assert "availability coverage" in render_gap_markdown(r)


def test_synthetic_closure_control_cost_and_columns(suite):
    r = report(suite)
    assert r["pricing_coverage"]["brave"]["unobserved_cells_n"] > 0
    assert r["pricing_coverage"]["brave"]["coverage"] is None
    assert "## Vendor pricing coverage" in render_gap_markdown(r)
    native = headline(r, "native")
    assert native["gap_closure"]["value"] == 0.5
    assert native["gap_closure"]["pairs_n"] == 4
    assert headline(r, "floor")["gap_closure"]["value"] == 0
    assert headline(r, "ceiling")["gap_closure"]["value"] == 1
    assert native["pass_rate"] == 0.5
    assert 0 < native["pass_ci_95"]["low"] < 0.5 < native["pass_ci_95"]["high"] < 1
    assert native["escaped_defects_per_cell"] == 1
    assert native["visible_regressions"] == 1
    assert native["tokens"]["per_task"] == 340
    assert native["tokens"]["per_success"] == 680
    assert native["tokens"]["mix"] == dict(input=100, cached_input=200, output=40)
    assert native["searched_on_gap_rate"] == 1
    brave = headline(r, "brave")
    assert brave["control"]["harm_pass_rate_delta"] == -0.5
    assert brave["control"]["extra_tokens"] == 50
    assert brave["control"]["extra_search_calls"] == 2
    assert brave["pricing_lower_bound"] is True
    assert brave["dollars_per_success"] == pytest.approx(0.00148)
    assert r["excluded_task_cells"] == {"retired": 8}
    assert {t["verdict"] for t in r["calibration"]["tasks"]} == {"admitted", "rejected", "retired"}
    assert set(r["by_family"]) == {"api-break", "decision-brief", "control"}
    c = next(c for c in r["comparisons"] if c["arm"] == "native" and c["baseline"] == "floor")
    assert c["pairs_n"] == 4 and c["wins"] == 2 and c["losses"] == 0 and c["p_exact"] == 0.5
    markdown = render_gap_markdown(r)
    for column in (
        "gap closure",
        "pass (Wilson",
        "escaped defects/cell",
        "visible regressions",
        "tokens/cell",
        "tokens/success",
        "mix in/cache/out",
        "$/success",
        "searched on gap",
        "control harm",
        "control search calls",
        "control extra tokens",
        "cells / marks",
    ):
        assert column in markdown
    assert "≥ " in markdown
    assert "retired" in markdown
    assert "winner" not in markdown.lower()
    assert json.loads(json.dumps(r)) == r


@pytest.mark.parametrize(
    "field,value",
    [(field, None) for field in ("hidden", "visible", "reruns", "flakes", "agreement")]
    + [("agreement", {"disagreements": None})],
)
def test_explain_null_optional_fields(suite, field, value):
    path = suite / "bundles/gap-a-native-1/evaluations/gap-outcome.json"
    evaluation = json.loads(path.read_text())
    evaluation[field] = value
    _write(path, evaluation)
    r = report(suite)
    data, markdown = explain_gap(r, task="gap-a", arm="native")
    assert data["cells"][0]["evaluation"][field] == value
    assert "rep 1  PASS" in markdown
    expected = {
        "hidden": "escaped: 0",
        "visible": "visible tests 0/unknown",
        "reruns": "reruns 0",
        "flakes": "flakes []",
        "agreement": "kappa —; disagreement []",
    }
    assert expected[field] in markdown.split("rep 2")[0]


@pytest.mark.parametrize("lane", ["hidden", "visible"])
def test_explain_null_lane_tests(suite, lane):
    path = suite / "bundles/gap-a-native-1/evaluations/gap-outcome.json"
    evaluation = json.loads(path.read_text())
    evaluation[lane] = [{"tests": None}]
    _write(path, evaluation)
    _, markdown = explain_gap(report(suite), task="gap-a", arm="native")
    assert "escaped: 0" in markdown
    if lane == "visible":
        assert "visible tests 0/unknown" in markdown


def test_explain_cli_and_artifacts(suite, capsys):
    r = report(suite)
    data, markdown = explain_gap(r, task="gap-a", arm="native")
    assert [c["rep"] for c in data["cells"]] == [1, 2]
    assert (
        "test_timeout" in markdown and "visible tests 1/1" in markdown and "searches 2" in markdown
    )
    assert main(["gap", "report", str(suite)]) == 0
    assert (suite / "reports/gap-report.json").is_file()
    assert (suite / "reports/gap-report.md").read_text().startswith("# GAP report:")
    assert (
        main(
            ["gap", "explain", str(suite), "--task", "gap-b", "--arm", "native", "--format", "json"]
        )
        == 0
    )
    assert "cohens_kappa" in capsys.readouterr().out
    with pytest.raises(ReportError, match="no cells"):
        explain_gap(r, task="missing", arm="native")


@pytest.mark.parametrize(
    "parts, expected",
    [
        ({"input": 100, "output": 30}, "100/—/30"),
        ({"input": 100, "reasoning": 10}, "100/—/10"),
        ({"input": 100}, "100/—/0"),
        ({"input": 100, "cached_input": 200, "output": 30, "reasoning": 10}, "100/200/40"),
        ({}, "—"),
        (None, "—"),
    ],
)
def test_explain_sparse_token_parts(suite, parts, expected):
    r = report(suite)
    cell = next(c for c in r["cells"] if c["task_id"] == "gap-a" and c["arm"] == "native")
    cell["token_parts"] = parts
    data, markdown = explain_gap(r, task="gap-a", arm="native")
    assert f"in/cache/out {expected})" in markdown
    assert data["cells"][0]["token_parts"] == parts


def test_markdown_coverage_is_readable_and_preserves_missing_measurements(suite):
    metrics_path = suite / "bundles/gap-a-native-1/metrics/metrics.json"
    metrics = json.loads(metrics_path.read_text())
    metrics["token_usage"]["accounting_source"] = "unknown"
    _write(metrics_path, metrics)
    index = json.loads((suite / "run-index.json").read_text())
    _write(suite / "run-index.json", [e for e in index if e["run_id"] != "gap-a-floor-1"])
    r = report(suite)
    markdown = render_gap_markdown(r)
    coverage = markdown.split("## Pairing and measurement coverage", 1)[1].split(
        "## Calibration", 1
    )[0]
    native = next(line for line in coverage.splitlines() if line.startswith("- native:"))
    assert "closure 2 tasks / 3 paired cells; excluded 1" in native
    assert "controls 2 cells / 2 pairs; excluded 0" in native
    assert "control tokens paired 2/2; control searches measured 2/2" in native
    assert "tokens measured 3/4; unknown 1; estimated 0; mix 3/4" in native
    assert "defects measured 2/4; visible measured 2/4; priced 3/4" in native
    assert "{" not in markdown and "}" not in markdown
    assert "retired: 8" in markdown
    assert r["headline"] and headline(r, "native")["gap_closure"]["excluded_pairs"]


@pytest.mark.parametrize(
    "mode",
    [
        "contaminated",
        "ungraded",
        "budget_exhausted",
        "unknown_usage",
        "estimated_usage",
        "missing_reference",
    ],
)
def test_no_silent_cell_drop(suite, mode):
    path = suite / "bundles/gap-a-native-1"
    if mode == "missing_reference":
        index = json.loads((suite / "run-index.json").read_text())
        (suite / "run-index.json").write_text(
            json.dumps([e for e in index if e["run_id"] != "gap-a-floor-1"])
        )
    elif mode in ("contaminated", "budget_exhausted"):
        run = json.loads((path / "run.json").read_text())
        run["status"] = mode
        _write(path / "run.json", run)
    elif mode == "ungraded":
        _write(path / "evaluations/gap-outcome.json", {"outcome": "not_applicable"})
    else:
        metrics = json.loads((path / "metrics/metrics.json").read_text())
        metrics["token_usage"]["accounting_source"] = (
            "estimated" if mode == "estimated_usage" else "unknown"
        )
        _write(path / "metrics/metrics.json", metrics)
    r = report(suite)
    native = headline(r, "native")
    assert native["cells_n"] == 4
    assert len([c for c in r["cells"] if c["arm"] == "native"]) == 6
    if mode in ("contaminated", "ungraded", "missing_reference"):
        assert native["gap_closure"]["excluded_pairs"]
        assert native["gap_closure"]["pairs_n"] == 3
    if mode == "ungraded":
        assert native["pass_rate"] is None
        assert native["marks"]["ungraded"] == 1
    if mode == "budget_exhausted":
        assert native["budget_exhausted_n"] == 1 and native["pass_rate"] == 0.25
    if mode.endswith("usage"):
        assert native["tokens"]["measured_n"] == 3
        assert native["pricing_lower_bound"]


@pytest.mark.parametrize("mode", ["duplicate", "missing_rep", "model", "harness", "calibration"])
def test_invalid_pair_identity_refused(suite, mode):
    index = json.loads((suite / "run-index.json").read_text())
    if mode == "duplicate":
        index.append(index[0])
    if mode == "missing_rep":
        index[0].pop("repetition")
    if mode in ("duplicate", "missing_rep"):
        (suite / "run-index.json").write_text(json.dumps(index))
    if mode in ("model", "harness", "calibration"):
        state = json.loads((suite / "runner-state.json").read_text())
        if mode == "calibration":
            state.pop("calibration")
        else:
            state["calibration"][mode] = "wrong"
        _write(suite / "runner-state.json", state)
    with pytest.raises(ReportError):
        report(suite)


def test_scipy_reference_variable_reference_gap():
    # SciPy 1.18.1 paired percentile bootstrap, 10,000 draws with the same
    # Python Random(42) task indices: (0.425, 0.9142857142857144).
    # Reproduce with tests/fixtures/gap/statistics_reference.py.
    triples = [(0.8, 0.1, 1), (0.6, 0.2, 0.9), (0.4, 0, 0.7), (0.9, 0.1, 0.8), (0.3, 0.1, 0.9)]
    ci = paired_bootstrap(triples, seed=42)["ci_95"]
    assert ci["low"] == pytest.approx(0.425)
    assert ci["high"] == pytest.approx(0.9142857142857144)


def test_missing_metadata_kept_unscored(suite):
    (suite / "bundles/gap-a-native-1/artifacts/spawn-metadata.json").unlink()
    r = report(suite)
    native = headline(r, "native")
    assert native["cells_n"] == 4
    assert native["marks"]["model_identity_missing"] == 1
    assert native["gap_closure"]["pairs_n"] == 3
    assert native["searched_on_gap_rate"] is None
    assert native["search_calls_measured_n"] == 3


def test_control_family_has_outcomes_and_tokens(suite):
    r = report(suite)
    control = next(row for row in r["by_family"]["control"] if row["arm"] == "brave")
    assert control["scope"] == "control" and control["pass_rate"] == 0.5
    assert control["tokens"]["per_task"] == 390
    assert control["gap_closure"]["value"] is None
    assert r["comparisons_by_family"]["api-break"][0]["pairs_n"] == 2


def test_no_success_cost_and_tokens_undefined(suite):
    floor = headline(report(suite), "floor")
    assert floor["tokens"]["per_success"] is None
    assert floor["dollars_per_success"] is None


def test_calibration_grade_and_explicit_relative_record(suite):
    path = suite / "bundles/gap-a-native-1/evaluations"
    (path / "gap-outcome.json").rename(path / "calibration-outcome.json")
    state = json.loads((suite / "runner-state.json").read_text())
    (suite / "calibration.json").write_text(json.dumps(state["calibration"]))
    _write(suite / "runner-state.json", {"calibration": "calibration.json"})
    assert headline(report(suite), "native")["pass_rate"] == 0.5


def test_every_column_json_field(suite):
    row = headline(report(suite), "native")
    paths = [
        ("arm",),
        ("gap_closure", "value"),
        ("gap_closure", "ci_95"),
        ("pass_rate",),
        ("pass_ci_95",),
        ("escaped_defects_per_cell",),
        ("visible_regressions",),
        ("tokens", "per_task"),
        ("tokens", "per_success"),
        ("tokens", "mix"),
        ("dollars_per_success",),
        ("pricing_lower_bound",),
        ("searched_on_gap_rate",),
        ("control", "harm_pass_rate_delta"),
        ("control", "search_calls_per_cell"),
        ("control", "extra_search_calls"),
        ("control", "extra_tokens"),
        ("cells_n",),
        ("marks",),
        ("budget_exhausted_n",),
    ]
    for path in paths:
        obj = row
        for key in path:
            assert key in obj, path
            obj = obj[key]


def test_tasks_equally_weighted_and_reps_stay_clustered(suite):
    index = json.loads((suite / "run-index.json").read_text())
    # gap-a has two reps (arm .5); gap-b one rep (arm 1). Task mean = .75,
    # rather than the cell-weighted 2/3. CI resamples these two clusters.
    (suite / "run-index.json").write_text(
        json.dumps([e for e in index if not (e["task_id"] == "gap-b" and e["repetition"] == 2)])
    )
    closure = headline(report(suite), "native")["gap_closure"]
    assert closure["value"] == 0.75
    assert closure["ci_95"] == {"low": 0.5, "high": 1.0}
    assert closure["tasks_n"] == 2 and closure["pairs_n"] == 3


def test_unknown_model_price_is_lower_bound_not_free(suite):
    from sew.cost_model import parse_price_table

    r = build_gap_report(
        suite,
        module_base=MODULE_ROOT,
        price_table=parse_price_table({"version": 1, "models": {}, "vendors": {}}),
    )
    native = headline(r, "native")
    assert native["dollars_per_success"] is None
    assert native["pricing_lower_bound"]
    assert native["priced_cells_n"] == 0


@pytest.mark.parametrize("lane", ["gap-a", "control"])
def test_missing_search_telemetry_unknown_not_zero(suite, lane):
    path = suite / f"bundles/{lane}-native-1/artifacts/spawn-metadata.json"
    spawn = json.loads(path.read_text())
    spawn.pop("process")
    _write(path, spawn)
    native = headline(report(suite), "native")
    if lane == "gap-a":
        assert native["searched_on_gap_rate"] is None
        assert native["search_calls_measured_n"] == 3
        assert native["gap_closure"]["pairs_n"] == 4
    else:
        assert native["control"]["search_calls_per_cell"] is None
        assert native["control"]["extra_search_calls"] is None
        assert native["control"]["search_calls_measured_n"] == 1
        assert native["control"]["harm_pass_rate_delta"] == 0


def test_infrastructure_failure_missing_bundle_counted(suite):
    index = json.loads((suite / "run-index.json").read_text())
    e = next(e for e in index if e["run_id"] == "gap-a-native-1")
    e["status"] = "provider_unavailable"
    e.pop("run_dir")
    (suite / "run-index.json").write_text(json.dumps(index))
    native = headline(report(suite), "native")
    assert native["cells_n"] == 4
    assert native["marks"]["provider_unavailable"] == 1
    assert native["status_counts"]["provider_unavailable"] == 1
    assert native["gap_closure"]["pairs_n"] == 3


def test_audit_overrides_passing_grade_and_verifier_errors_ungraded(suite):
    path = suite / "bundles/gap-a-native-1/artifacts/spawn-metadata.json"
    spawn = json.loads(path.read_text())
    spawn["arm_audit"]["contaminated"] = True
    _write(path, spawn)
    _write(
        suite / "bundles/gap-b-native-1/evaluations/gap-outcome.json",
        {"outcome": "pass", "errors": [{"stage": "setup", "message": "broken environment"}]},
    )
    native = headline(report(suite), "native")
    assert native["marks"]["contaminated"] == 1
    assert native["marks"]["ungraded"] == 1
    assert native["pass_rate"] is None


def test_report_cannot_mix_model_profiles(suite):
    path = suite / "bundles/gap-a-native-1/run.json"
    run = json.loads(path.read_text())
    run["model_profile"] = "other"
    _write(path, run)
    with pytest.raises(ReportError, match="profiles"):
        report(suite)


def test_gap04_execution_evaluation_envelope(suite):
    path = suite / "bundles/gap-a-native-2/evaluations"
    grade = json.loads((path / "gap-outcome.json").read_text())
    (path / "gap-outcome.json").unlink()
    evaluation = json.loads((path / "evaluation.json").read_text())
    evaluation["execution"] = grade
    _write(path / "evaluation.json", evaluation)
    r = report(suite)
    assert headline(r, "native")["escaped_defects_per_cell"] == 1
    data, markdown = explain_gap(r, task="gap-a", arm="native")
    assert data["cells"][1]["evaluation"] == grade
    assert "test_timeout" in markdown


def test_complete_pricing_not_labelled_lower_bound(suite):
    # Provide measured provider spend for every search cell: model + search
    # can now be fully priced (no changes to outcome or token populations).
    for path in (suite / "bundles").glob("*"):
        run = json.loads((path / "run.json").read_text())
        if run["provider_id"] not in ("native", "brave"):
            continue
        refs = []
        for i in range(2):
            ref = f"provider-calls/paid-{i}.json"
            refs.append(ref)
            _write(
                path / ref,
                {
                    "provider_id": run["provider_id"],
                    "status": "ok",
                    "operation": "search",
                    "ended_at": "2026-09-30T00:00:00Z",
                    "response": {"provider_cost": {"currency": "USD", "total": 0.01}},
                },
            )
        run["provider_call_refs"] = refs
        _write(path / "run.json", run)
    r = report(suite)
    brave = headline(r, "brave")
    assert brave["pricing_lower_bound"] is False
    assert brave["dollars_per_success"] == pytest.approx(0.04148)


def test_retirement_excludes_all_measurements_and_keeps_provenance(suite):
    before = report(suite)
    state = json.loads((suite / "runner-state.json").read_text())
    task = state["calibration"]["tasks"][0]
    task.update(
        verdict="retired", reason="floor rose above 0.2", date="2026-10-03", retired_at="2026-10-03"
    )
    after = build_gap_report(
        suite, module_base=MODULE_ROOT, price_table=PRICES, calibration=state["calibration"]
    )
    assert after["excluded_task_cells"]["retired"] == 16
    assert all(c["task_id"] != task["task_id"] for c in after["cells"])
    assert "api-break" not in after["by_family"]
    for arm in after["headline"]:
        prior = headline(before, arm["arm"])
        assert arm["cells_n"] == prior["cells_n"] - 2
        assert arm["gap_closure"]["pairs_n"] == prior["gap_closure"]["pairs_n"] - 2
    assert "floor rose above 0.2" in render_gap_markdown(after)
    assert "2026-10-03" in render_gap_markdown(after)


def test_report_exposes_denied_attempt_behavior(suite):
    root = suite
    metadata_path = root / "bundles/gap-a-floor-1/artifacts/spawn-metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata.setdefault("arm_audit", {})["denied_network_attempts"] = 2
    metadata["arm_audit"]["config_neutralized_network_attempts"] = 3
    _write(metadata_path, metadata)
    report = build_gap_report(root, price_table=PRICES)
    cell = next(c for c in report["cells"] if c["run_id"] == "gap-a-floor-1")
    assert cell["denied_network_attempts"] == 2
    assert cell["config_neutralized_network_attempts"] == 3
    assert "Denied network attempts: 2" in render_gap_markdown(report)
    assert "Config-neutralized network attempts: 3" in render_gap_markdown(report)


@pytest.mark.parametrize(
    "audit",
    [
        None,
        [],
        "bad",
        {"denied_network_attempts": "2"},
        {"denied_network_attempts": True},
        {"denied_network_attempts": -1},
    ],
)
def test_report_tolerates_malformed_arm_audit(suite, audit):
    path = suite / "bundles/gap-a-floor-1/artifacts/spawn-metadata.json"
    metadata = json.loads(path.read_text())
    metadata["arm_audit"] = audit
    _write(path, metadata)
    report = build_gap_report(suite, price_table=PRICES)
    cell = next(c for c in report["cells"] if c["run_id"] == "gap-a-floor-1")
    assert cell["denied_network_attempts"] == 0


@pytest.mark.parametrize("saved", [False, True])
def test_auth_failure_with_ready_marker_counts_as_unknown(suite, saved):
    from sew.mcp_meter import availability_record, write_availability

    entries = json.loads((suite / "run-index.json").read_text())
    for entry in entries:
        if entry["provider_id"] != "brave":
            continue
        directory = Path(entry["run_dir"])
        entry["status"] = "harness_boot_failed"
        record = json.loads((directory / "run.json").read_text())
        record.update(status="harness_boot_failed", failure_category="harness_auth_failed")
        if saved:
            record["provider_availability"] = "unknown"
        _write(directory / "run.json", record)
        snapshot = availability_record("brave", entry["run_id"])
        snapshot.update(wrapper_engaged=True, observation_reason="server_not_launched")
        write_availability(directory / "provider-calls", snapshot)
        _write(directory / "artifacts/transcript.json", [{"event": "harness_ready"}])
    _write(suite / "run-index.json", entries)
    counts = headline(report(suite), "brave")["availability"]
    assert counts["available_n"] == counts["unavailable_n"] == 0
    assert counts["unknown_n"] == counts["cells_n"] == 4
