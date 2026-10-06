"""Paired job outcomes; task-cluster percentile bootstrap, never an arm ranking.

Runner contract: runner-state.json + run-index.json, one entry per
(task_id, repetition, harness_id, model_profile, provider_id), with run_dir.
Outcome records live at evaluations/gap-outcome.json (calibration-outcome.json
is also supported), or at run.json's evaluation_ref. Calibration is embedded
as `calibration` in runner state or supplied explicitly. No grader is invoked.
"""

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import replace
import json
from pathlib import Path
import random
import statistics

from ..bakeoff_report import (
    _aggregate,
    _bakeoff_run,
    _optional_record,
    _priceable_usage,
)
from ..arms import PROVIDER_SERVER_NAMES
from ..mcp_meter import meter_observed, provider_available
from ..catalog import module_root
from ..cost_model import load_price_table, model_cost
from ..pricing_coverage import pricing_coverage, render_pricing_coverage
from ..report import (
    ReportError,
    ReportArtifacts,
    _load_state,
    _load_index,
    _load_rows,
    _percentile,
    _table,
)
from ..schema import load_document


def _denied_attempts(spawn, metric="denied_network_attempts"):
    audit = spawn.get("arm_audit")
    value = audit.get(metric, 0) if isinstance(audit, Mapping) else 0
    return value if type(value) is int and value >= 0 else 0


def exact_mcnemar(wins, losses):
    """Two-sided exact binomial McNemar, as in statsmodels' exact=True."""
    if any(type(n) is not int or n < 0 for n in (wins, losses)):
        raise ValueError("discordances must be nonnegative integers")
    n = wins + losses
    if wins == losses:
        return 1.0
    # Keep the numerator and denominator as integers until the final ratio.
    # Python integer true division scales both operands without converting the
    # huge denominator to float; an asymptotic approximation is unnecessary.
    # Recurrence avoids recomputing each binomial coefficient on large suites.
    coefficient = tail = 1
    for k in range(1, min(wins, losses) + 1):
        coefficient = coefficient * (n - k + 1) // k
        tail += coefficient
    return min(1.0, (2 * tail) / (1 << n))


def paired_bootstrap(triples, *, seed=0, resamples=10_000):
    """Ratio of task-mean deltas; resample whole tasks with replacement.

    Each triple is (arm, floor, ceiling) task pass rates on matched reps.
    Percentile CI follows scipy.stats.bootstrap(method='percentile', paired=True).
    Undefined draws are counted and withhold the CI, never silently discarded.
    """
    if type(resamples) is not int or resamples < 1:
        raise ValueError("resamples must be positive")

    def closure(sample):
        denominator = sum(c - f for a, f, c in sample)
        return sum(a - f for a, f, c in sample) / denominator if denominator > 0 else None

    point = closure(triples) if triples else None
    rng = random.Random(seed)
    draws = (
        [closure(rng.choices(triples, k=len(triples))) for _ in range(resamples)] if triples else []
    )
    invalid = sum(v is None for v in draws)
    ci = (
        {"low": _percentile(draws, 2.5), "high": _percentile(draws, 97.5)}
        if draws and not invalid
        else None
    )
    return {
        "value": point,
        "ci_95": ci,
        "tasks_n": len(triples),
        "seed": seed,
        "resamples": resamples,
        "undefined_resamples_n": invalid,
        "reason": "no_paired_tasks"
        if not triples
        else "nonpositive_reference_gap"
        if point is None
        else "undefined_resamples"
        if invalid
        else None,
    }


def _outcome(row):
    for ref in ("evaluations/gap-outcome.json", "evaluations/calibration-outcome.json"):
        record = _optional_record(row.run_dir, ref)
        if record is not None:
            return record
    run = _optional_record(row.run_dir, "run.json") or {}
    record = (
        _optional_record(row.run_dir, run["evaluation_ref"]) if run.get("evaluation_ref") else {}
    )
    # GAP-04's grade_run stores verifier evidence under evaluation.execution.
    if isinstance(record, dict) and isinstance(record.get("execution"), dict):
        return record["execution"]
    return record


def _pass(cell):
    disposition = cell["run"].disposition
    return disposition == "success" if disposition in {"success", "failure"} else None


def _pairs(cells, arm, baseline, *, kind):
    left = {(c["task_id"], c["rep"]): c for c in cells if c["arm"] == arm and c["kind"] == kind}
    right = {
        (c["task_id"], c["rep"]): c for c in cells if c["arm"] == baseline and c["kind"] == kind
    }
    valid, excluded = [], []
    for key in sorted(left.keys() | right.keys()):
        a, b = left.get(key), right.get(key)
        if a is None or b is None or _pass(a) is None or _pass(b) is None:
            excluded.append(
                {"task_id": key[0], "rep": key[1], "reason": "missing_or_unscored_pair"}
            )
        else:
            valid.append((a, b))
    return valid, excluded


def _comparison(cells, arm, baseline):
    pairs, excluded = _pairs(cells, arm, baseline, kind="gap")
    wins = sum(_pass(a) and not _pass(b) for a, b in pairs)
    losses = sum(_pass(b) and not _pass(a) for a, b in pairs)
    return {
        "arm": arm,
        "baseline": baseline,
        "pairs_n": len(pairs),
        "excluded_pairs": excluded,
        "wins": wins,
        "losses": losses,
        "pass_rate_delta": (wins - losses) / len(pairs) if pairs else None,
        "p_exact": exact_mcnemar(wins, losses) if pairs else None,
    }


def _summary(cells, arm, seed):
    scope = "gap" if any(c["kind"] == "gap" for c in cells) else "control"
    own = [c for c in cells if c["arm"] == arm and c["kind"] == scope]
    runs = [c["run"] for c in own]
    aggregate = _aggregate(runs, arm_key=("", arm, ""), task_class="gap")
    lookup = {(c["task_id"], c["rep"], c["arm"]): c for c in cells if c["kind"] == "gap"}
    clustered = defaultdict(list)
    excluded = []
    for task, rep in sorted({(c["task_id"], c["rep"]) for c in cells if c["kind"] == "gap"}):
        triple = [lookup.get((task, rep, a)) for a in (arm, "floor", "ceiling")]
        if any(c is None or _pass(c) is None for c in triple):
            excluded.append({"task_id": task, "rep": rep, "reason": "missing_or_unscored_triplet"})
        else:
            clustered[task].append([int(_pass(c)) for c in triple])
    triples = [
        tuple(statistics.fmean(t[i] for t in values) for i in range(3))
        for values in clustered.values()
    ]
    closure = paired_bootstrap(triples, seed=seed)
    closure.update(
        pairs_n=sum(map(len, clustered.values())),
        excluded_pairs=excluded,
        reference=arm in {"floor", "ceiling"},
    )
    graded = [c for c in own if _pass(c) is not None]
    defects = [
        c["evaluation"]["escaped_defects"] for c in graded if "escaped_defects" in c["evaluation"]
    ]
    regressions = [
        c["evaluation"]["visible_regressions"]
        for c in graded
        if "visible_regressions" in c["evaluation"]
    ]
    pairs, control_excluded = _pairs(cells, arm, "floor", kind="control")
    measured = [
        (a["run"].tokens, b["run"].tokens)
        for a, b in pairs
        if a["run"].tokens is not None and b["run"].tokens is not None
    ]
    controls = [c for c in cells if c["arm"] == arm and c["kind"] == "control"]
    costed = [c["known_usd"] for c in own if c["known_usd"] is not None]
    partial = any(c["partial_cost"] for c in own)
    success = aggregate["successes"]
    return {
        "arm": arm,
        "scope": scope,
        "gap_closure": closure,
        "cells_n": len(own),
        "metering": {
            "observed_n": sum(c["meter_observed"] for c in own),
            "cells_n": len(own),
            "coverage": sum(c["meter_observed"] for c in own) / len(own) if own else None,
        },
        "availability": {
            "available_n": sum(c["provider_available"] is True for c in own),
            "unavailable_n": sum(c["provider_available"] is False for c in own),
            "unknown_n": sum(c["provider_available"] is None for c in own),
            "cells_n": len(own),
            "coverage": sum(c["provider_available"] is True for c in own) / len(own)
            if own and arm in PROVIDER_SERVER_NAMES
            else None,
        },
        "pass_rate": aggregate["success_rate"],
        "pass_ci_95": aggregate["success_ci_95"],
        "graded_pass_rate": aggregate["graded_success_rate"],
        "successes_n": success,
        "marks": dict(Counter(c["run"].disposition for c in own)),
        "status_counts": dict(Counter(c["run"].row.status for c in own)),
        "budget_exhausted_n": sum(c["run"].row.status == "budget_exhausted" for c in own),
        "escaped_defects_per_cell": statistics.fmean(defects) if defects else None,
        "escaped_defects_measured_n": len(defects),
        "visible_regressions": sum(regressions) if regressions else None,
        "visible_regressions_measured_n": len(regressions),
        "tokens": aggregate["tokens"],
        "dollars_per_success": sum(costed) / success
        if success and costed and not aggregate["ungraded_n"]
        else None,
        "pricing_lower_bound": partial,
        "priced_cells_n": len(costed),
        "searched_on_gap_rate": sum(c["run"].search_calls > 0 for c in own) / len(own)
        if own and all(c["search_calls_known"] for c in own)
        else None,
        "search_calls_measured_n": sum(c["search_calls_known"] for c in own),
        "control": {
            "cells_n": len(controls),
            "marks": dict(Counter(c["run"].disposition for c in controls)),
            "budget_exhausted_n": sum(c["run"].row.status == "budget_exhausted" for c in controls),
            "pairs_n": len(pairs),
            "excluded_pairs": control_excluded,
            "harm_pass_rate_delta": statistics.fmean(
                int(_pass(a)) - int(_pass(b)) for a, b in pairs
            )
            if pairs
            else None,
            "search_calls_per_cell": statistics.fmean(c["run"].search_calls for c in controls)
            if controls and all(c["search_calls_known"] for c in controls)
            else None,
            "search_calls_measured_n": sum(c["search_calls_known"] for c in controls),
            "extra_search_calls": statistics.fmean(
                a["run"].search_calls - b["run"].search_calls for a, b in pairs
            )
            if pairs and all(a["search_calls_known"] and b["search_calls_known"] for a, b in pairs)
            else None,
            "extra_tokens": statistics.fmean(a - b for a, b in measured) if measured else None,
            "tokens_paired_n": len(measured),
        },
    }


def build_gap_report(run_root, *, module_base=None, calibration=None, price_table=None, seed=0):
    run_root = Path(run_root)
    base = Path(module_base or module_root())
    state = _load_state(run_root)
    calibration = calibration or state.get("calibration")
    if isinstance(calibration, (str, Path)):
        path = Path(calibration)
        calibration = load_document(path if path.is_absolute() else run_root / path)
    if not isinstance(calibration, dict) or not isinstance(calibration.get("tasks"), list):
        raise ReportError(
            "GAP report requires a calibration record (--calibration or runner state)"
        )
    tasks = {t["task_id"]: t for t in calibration["tasks"]}
    table = price_table or load_price_table(base / "config/price-table.yaml")
    rows = _load_rows(
        _load_index(run_root, state),
        {t: v["family"] for t, v in tasks.items()},
        link_base=run_root / "reports",
    )
    if len({row.model_profile for row in rows}) > 1:
        raise ReportError("GAP report cannot pool different model profiles")
    cells, seen = [], set()
    excluded_tasks = Counter()
    excluded_cells = []
    for row in rows:
        task = tasks.get(row.task_id)
        if task is None:
            raise ReportError(f"task absent from calibration: {row.task_id}")
        if row.harness_id != calibration["harness"]:
            raise ReportError("calibration harness does not match suite")
        if row.provider_id not in {*PROVIDER_SERVER_NAMES, "native", "floor", "ceiling"}:
            raise ReportError("non-GAP arm in run index")
        spawn = _optional_record(row.run_dir, "artifacts/spawn-metadata.json") or {}
        if spawn.get("model_id") is not None and spawn["model_id"] != calibration["model"]:
            raise ReportError("mismatched calibrated model identity")
        key = (row.task_id, row.repetition, row.provider_id)
        if type(row.repetition) is not int or row.repetition < 1 or key in seen:
            raise ReportError("GAP needs unique (task, rep, arm) cells")
        seen.add(key)
        if task["verdict"] != "admitted":
            excluded_tasks[task["verdict"]] += 1
            excluded_cells.append(
                {
                    "task_id": row.task_id,
                    "rep": row.repetition,
                    "arm": row.provider_id,
                    "run_id": row.run_id,
                    "reason": task["verdict"],
                    "denied_network_attempts": _denied_attempts(spawn),
                    "config_neutralized_network_attempts": _denied_attempts(
                        spawn, "config_neutralized_network_attempts"
                    ),
                }
            )
            continue
        evaluation = _outcome(row) or {}
        outcome = evaluation.get("outcome") if not evaluation.get("errors") else None
        row = replace(
            row,
            evaluation_outcome=outcome,
            successful=outcome == "pass" and row.status == "succeeded",
        )
        run = _bakeoff_run(row, table, run_root / "reports")
        if spawn.get("model_id") is None and run.disposition in {"success", "failure", "ungraded"}:
            run = replace(run, disposition="model_identity_missing")
        known = None
        partial = True
        if run.cost is not None:
            amounts = [
                c["amount_usd"] for c in run.cost["components"] if c.get("amount_usd") is not None
            ]
            known = sum(amounts) if amounts else None
            partial = run.cost["total_usd"] is None
        elif run.tokens is not None:
            usage, _ = _priceable_usage(row.token_usage)
            component = model_cost(run.model_id, usage, table) if usage else {}
            known = component.get("amount_usd")
        search_known = "provider_calls" in spawn.get("process", {}) or row.provider_id in {
            "floor",
            "ceiling",
        }
        if not search_known:
            partial = True
        cells.append(
            {
                "task_id": row.task_id,
                "rep": row.repetition,
                "arm": row.provider_id,
                "kind": task["kind"],
                "family": task["family"],
                "run": run,
                "evaluation": evaluation,
                "denied_network_attempts": _denied_attempts(spawn),
                "config_neutralized_network_attempts": _denied_attempts(
                    spawn, "config_neutralized_network_attempts"
                ),
                "known_usd": known,
                "partial_cost": partial,
                "search_calls_known": search_known,
                "meter_observed": meter_observed(row.run_dir, row.provider_id, row.run_id),
                "provider_available": False
                if row.status == "provider_unavailable"
                else provider_available(row.run_dir, row.provider_id, row.run_id)
                if row.provider_id in PROVIDER_SERVER_NAMES and row.run_dir is not None
                else None,
            }
        )
    arms = sorted({c["arm"] for c in cells})
    headline = [_summary(cells, a, seed) for a in arms]
    families = {
        family: [_summary([c for c in cells if c["family"] == family], a, seed) for a in arms]
        for family in sorted({c["family"] for c in cells})
    }
    comparisons = [_comparison(cells, a, b) for a in arms for b in ("native", "floor") if a != b]
    return {
        "schema_version": "gap-report-v1",
        "harness": calibration["harness"],
        "model": calibration["model"],
        "seed": seed,
        "headline": headline,
        "by_family": families,
        "comparisons_by_family": {
            family: [
                _comparison([c for c in cells if c["family"] == family], a, b)
                for a in arms
                for b in ("native", "floor")
                if a != b
            ]
            for family in families
        },
        "comparisons": comparisons,
        "calibration": calibration,
        "excluded_task_cells": dict(excluded_tasks),
        "excluded_cells": excluded_cells,
        "pricing_coverage": pricing_coverage([c["run"].row for c in cells], table),
        "definitions": {
            "gap_closure": "ratio of equally weighted task-mean paired deltas; 10,000 task-cluster percentile resamples",
            "pairing": "exact (task, rep); exclusions listed; no multiplicity correction",
            "pass_rate": "passed / attempted; withheld for ungraded cells; contaminants and infrastructure marked",
            "availability": "completed matching provider transcript calls prove availability; otherwise observed initialize and tools/list decide availability; an engaged unlaunched wrapper is unavailable only after harness success or a first-output marker; other missing discovery stays unknown and graded; run.json.provider_availability preserves the live verdict before transcript overflow; legacy bundles apply the same terminal filter; snapshot-write failures without completed calls stay unknown; availability.json is a child-observed discovery snapshot",
            "cost": "known spend over all gap cells / successes; partial pricing is a lower bound",
            "tokens": "SEWTOK-01 measured tokens; output mix includes reasoning; unknown usage is never zero",
        },
        "cells": [
            {k: v for k, v in c.items() if k != "run"}
            | {
                "run_id": c["run"].row.run_id,
                "run_dir": str(c["run"].row.run_dir) if c["run"].row.run_dir is not None else None,
                "status": c["run"].row.status,
                "disposition": c["run"].disposition,
                "tokens": c["run"].tokens,
                "token_parts": c["run"].token_parts,
                "search_calls": c["run"].search_calls,
                "cost": c["run"].cost,
                "cost_exclusions": list(c["run"].cost_exclusions)
                + list((c["run"].cost or {}).get("unknown_reasons", [])),
            }
            for c in cells
        ],
    }


def _fmt(value):
    return "—" if value is None else f"{value:.3g}" if isinstance(value, float) else str(value)


def _rate(value):
    return "—" if value is None else f"{100 * value:.0f}%"


def _pp(value):
    return "—" if value is None else f"{100 * value:+.1f} pp"


def _interval(point, ci, *, rate=False):
    show = _rate if rate else _fmt
    return show(point) + (f" ({show(ci['low'])}–{show(ci['high'])})" if ci else "")


def _mix(parts):
    return (
        "/".join(_fmt(parts.get(k)) for k in ("input", "cached_input", "output")) if parts else "—"
    )


def _counts(counts):
    return ", ".join(f"{name}: {count}" for name, count in sorted(counts.items())) or "none"


def _arm_table(rows):
    values = []
    for r in rows:
        closure = r["gap_closure"]
        closure_text = _interval(closure["value"], closure["ci_95"])
        if closure["reference"] and closure["value"] is not None:
            closure_text = f"{closure['value']:.2f} (reference)"
        elif closure["reason"]:
            closure_text += f" ({closure['reason']})"
        dollars = r["dollars_per_success"]
        dollars_text = (
            "—"
            if dollars is None
            else ("≥ " if r["pricing_lower_bound"] else "") + f"${dollars:.4f}"
        )
        values.append(
            [
                r["arm"],
                f"{r['availability']['available_n']}/{r['availability']['cells_n']}"
                if r["arm"] in PROVIDER_SERVER_NAMES
                else "n/a",
                f"{r['metering']['observed_n']}/{r['metering']['cells_n']}"
                if r["arm"] in PROVIDER_SERVER_NAMES
                else "n/a",
                closure_text,
                _interval(r["pass_rate"], r["pass_ci_95"], rate=True),
                _fmt(r["escaped_defects_per_cell"]),
                _fmt(r["visible_regressions"]),
                _fmt(r["tokens"]["per_task"]),
                _fmt(r["tokens"]["per_success"]),
                _mix(r["tokens"]["mix"]),
                dollars_text,
                _rate(r["searched_on_gap_rate"]),
                _pp(r["control"]["harm_pass_rate_delta"]),
                f"{_fmt(r['control']['search_calls_per_cell'])} / {_fmt(r['control']['extra_search_calls'])}",
                _fmt(r["control"]["extra_tokens"]),
                f"{r['cells_n']} / {_counts(r['marks'])}; budget exhausted {r['budget_exhausted_n']}",
            ]
        )
    return _table(
        [
            "arm",
            "availability coverage",
            "metering coverage",
            "gap closure (95% CI)",
            "pass (Wilson 95% CI)",
            "escaped defects/cell",
            "visible regressions",
            "tokens/cell",
            "tokens/success",
            "mix in/cache/out",
            "$/success",
            "searched on gap",
            "control harm",
            "control search calls / extra",
            "control extra tokens",
            "cells / marks",
        ],
        values,
    )


def render_gap_markdown(report):
    lines = [
        f"# GAP report: {report['harness']}@{report['model']}",
        "",
        f"{len({c['task_id'] for c in report['cells'] if c['kind'] == 'gap'})} gap tasks + "
        f"{len({c['task_id'] for c in report['cells'] if c['kind'] == 'control'})} controls; "
        f"reps {', '.join(map(str, sorted({c['rep'] for c in report['cells']})))}. "
        "Headline outcomes and usage cover gap tasks; control overhead and harm are separate.",
        "",
        render_pricing_coverage(report.get("pricing_coverage", {})),
        "",
        _arm_table(report["headline"]),
        "",
        "## Paired exact McNemar (task × rep)",
        _table(
            ["arm", "baseline", "delta", "p exact", "pairs", "excluded"],
            [
                [
                    r["arm"],
                    r["baseline"],
                    _pp(r["pass_rate_delta"]),
                    _fmt(r["p_exact"]),
                    r["pairs_n"],
                    len(r["excluded_pairs"]),
                ]
                for r in report["comparisons"]
            ],
        ),
    ]
    for family, rows in report["by_family"].items():
        lines.extend(
            [
                "",
                f"## Family: {family}",
                _arm_table(rows),
                _table(
                    ["arm", "baseline", "delta", "p exact", "pairs", "excluded"],
                    [
                        [
                            r["arm"],
                            r["baseline"],
                            _pp(r["pass_rate_delta"]),
                            _fmt(r["p_exact"]),
                            r["pairs_n"],
                            len(r["excluded_pairs"]),
                        ]
                        for r in report["comparisons_by_family"][family]
                    ],
                ),
            ]
        )
    lines.extend(["", "## Pairing and measurement coverage"])
    for row in report["headline"]:
        closure, control, tokens = row["gap_closure"], row["control"], row["tokens"]
        lines.append(
            f"- {row['arm']}: closure {closure['tasks_n']} tasks / {closure['pairs_n']} paired cells; "
            f"excluded {len(closure['excluded_pairs'])}; "
            f"undefined bootstrap draws {closure['undefined_resamples_n']}/{closure['resamples']}; "
            f"controls {control['cells_n']} cells / {control['pairs_n']} pairs; "
            f"excluded {len(control['excluded_pairs'])}; "
            f"control marks {_counts(control['marks'])}; budget exhausted {control['budget_exhausted_n']}; "
            f"control tokens paired {control['tokens_paired_n']}/{control['pairs_n']}; "
            f"control searches measured {control['search_calls_measured_n']}/{control['cells_n']}; "
            f"tokens measured {tokens['measured_n']}/{tokens['attempted_n']}; "
            f"unknown {tokens['unknown_n']}; estimated {tokens['estimated_n']}; "
            f"mix {tokens['mix_n']}/{tokens['attempted_n']}; "
            f"tokens/success coverage {tokens['per_success_n']} graded measured cells / "
            f"{tokens['per_success_successes_n']} successes; "
            f"searches measured {row['search_calls_measured_n']}/{row['cells_n']}; "
            f"defects measured {row['escaped_defects_measured_n']}/{row['cells_n']}; "
            f"visible measured {row['visible_regressions_measured_n']}/{row['cells_n']}; "
            f"priced {row['priced_cells_n']}/{row['cells_n']}."
        )
    lines.extend(
        [
            "",
            "## Calibration appendix",
            _table(
                ["task", "family", "kind", "floor", "ceiling", "verdict", "reason", "date"],
                [
                    [
                        t.get("task_id"),
                        t.get("family"),
                        t.get("kind"),
                        _rate(t.get("floor", {}).get("rate")),
                        _rate(t.get("ceiling", {}).get("rate")),
                        t.get("verdict"),
                        t.get("reason"),
                        t.get("date"),
                    ]
                    for t in report["calibration"]["tasks"]
                ],
            ),
            f"Excluded task cells: {_counts(report['excluded_task_cells'])}",
            f"Denied network attempts: {sum(c.get('denied_network_attempts', 0) for c in [*report['cells'], *report.get('excluded_cells', [])])}",
            f"Config-neutralized network attempts: {sum(c.get('config_neutralized_network_attempts', 0) for c in [*report['cells'], *report.get('excluded_cells', [])])}",
            "",
            "## Definitions",
        ]
    )
    lines.extend(f"- {k}: {v}" for k, v in report["definitions"].items())
    return "\n".join(lines) + "\n"


def generate_gap_report(run_root, *, output_dir=None, **kwargs):
    report = build_gap_report(run_root, **kwargs)
    destination = Path(output_dir or Path(run_root) / "reports")
    destination.mkdir(parents=True, exist_ok=True)
    artifacts = ReportArtifacts(destination / "gap-report.json", destination / "gap-report.md")
    artifacts.json_path.write_text(json.dumps(report, indent=2) + "\n")
    artifacts.markdown_path.write_text(render_gap_markdown(report))
    return artifacts


def explain_gap(report, *, task, arm):
    cells = sorted(
        (c for c in report["cells"] if c["task_id"] == task and c["arm"] == arm),
        key=lambda c: c["rep"],
    )
    if not cells:
        raise ReportError("no cells match task and arm")
    lines = []
    for c in cells:
        e = c["evaluation"]
        failed = [
            t["id"]
            for lane in (e.get("hidden") or [])
            for t in (lane.get("tests") or [])
            if t["status"] in {"failure", "error"}
        ]
        visible = [t for lane in (e.get("visible") or []) for t in (lane.get("tests") or [])]
        lines.extend(
            [
                f"rep {c['rep']}  { {'success': 'PASS', 'failure': 'FAIL'}.get(c['disposition'], c['disposition'].upper()) }  escaped: {', '.join(failed) or _fmt(e.get('escaped_defects'))}",
                f"  visible tests {sum(t['status'] == 'pass' for t in visible)}/{len(visible) if visible else 'unknown'}  searches {c['search_calls'] if c['search_calls_known'] else 'unknown'}  tokens {_fmt(c['tokens'])} (in/cache/out {_mix({**c['token_parts'], 'output': c['token_parts'].get('output', 0) + c['token_parts'].get('reasoning', 0)}) if c['token_parts'] else '—'})",
                f"  status {c['status']}; visible regressions {_fmt(e.get('visible_regressions'))}; "
                f"reruns {len(e.get('reruns') or [])}; flakes {e.get('flakes') or []}",
            ]
        )
        if "key_fact_recall" in e or "agreement" in e:
            agreement = e.get("agreement") or {}
            lines.append(
                f"  decision correct {e.get('decision_correct')}; recall {_fmt(e.get('key_fact_recall'))}; "
                f"unsupported {_fmt(e.get('unsupported_claim_rate'))}; kappa {_fmt(agreement.get('cohens_kappa'))}; "
                f"disagreement {agreement.get('disagreements') or []}"
            )
        if e.get("errors"):
            lines.append(f"  verifier errors: {json.dumps(e['errors'])}")
    return {"task_id": task, "arm": arm, "cells": cells}, "\n".join(lines) + "\n"
