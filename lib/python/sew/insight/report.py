"""Score a graded insight run and write REPORT.md, summary.json and the set's run history.

Strict credit: an arm is credited with a cluster only when one of its own
claims in that cluster is supported by the page that claim cites. An insight is
a credited cluster the blind judge valued 1 or more; it is unique when exactly
one arm is credited. Everything here is offline arithmetic over grading files.
"""

from __future__ import annotations

import hashlib
import json
import random
from math import comb
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..retrieval_lane import pct
from .grade import load_claims, supported_claims
from .run import now, read_json, write_json

BOOTSTRAP = 2000


def sign_test(wins: int, losses: int) -> float:
    """Exact two-sided sign test; ties are dropped."""
    n = wins + losses
    if n == 0:
        return 1.0
    tail = sum(comb(n, k) for k in range(min(wins, losses) + 1)) / 2**n
    return min(1.0, 2 * tail)


def holm(pvalues: Sequence[float]) -> list[float]:
    order = sorted(range(len(pvalues)), key=lambda i: pvalues[i])
    adjusted, running = [0.0] * len(pvalues), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(pvalues) - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


def bootstrap_ci(diffs: Sequence[float], seed: int, n: int = BOOTSTRAP) -> tuple[float, float]:
    """95% percentile interval of the summed per-question difference."""
    if not diffs:
        return (0.0, 0.0)
    rng = random.Random(seed)
    totals = sorted(sum(rng.choice(diffs) for _ in diffs) for _ in range(n))
    return (totals[int(0.025 * n)], totals[int(0.975 * n) - 1])


def score(run_dir: Path) -> dict[str, Any]:
    manifest = read_json(run_dir / "manifest.json")
    grading = read_json(run_dir / "grading" / "grading.json")
    arms = sorted(manifest["profiles"])
    qids = manifest["questions"]
    cells = {}
    for path in (run_dir / "cells").glob("*/*.json"):
        doc = read_json(path)
        cells[(doc["question_id"], doc["arm_id"])] = doc
    claims = load_claims(run_dir)
    support = supported_claims(run_dir, claims)

    # Clusters with their credited arms and blind value.
    clusters: dict[str, dict[str, Any]] = {}
    for qid in qids:
        cpath, jpath = run_dir / "grading" / "clusters" / f"{qid}.json", run_dir / "grading" / "judge" / f"{qid}.json"
        if not cpath.is_file():
            continue
        values = read_json(jpath)["values"] if jpath.is_file() else {}
        for cl in read_json(cpath)["clusters"]:
            credited = sorted({m.split("#")[0] for m in cl["members"] if support.get(m) == 1})
            found = sorted({m.split("#")[0] for m in cl["members"]})
            value = values.get(cl["id"], {}).get("score") if credited else None
            clusters[cl["id"]] = {"question_id": qid, "credited": credited, "found_by": found,
                                  "value": value}
    insights = {cid: c for cid, c in clusters.items() if c["value"] and c["value"] >= 1}
    pooled = sum(c["value"] for c in insights.values())

    per_arm: dict[str, dict[str, Any]] = {}
    weighted_by_q = {a: {q: 0 for q in qids} for a in arms}
    for arm in arms:
        mine = {cid: c for cid, c in insights.items() if arm in c["credited"]}
        unique = {cid: c for cid, c in mine.items() if c["credited"] == [arm]}
        for c in mine.values():
            weighted_by_q[arm][c["question_id"]] += c["value"]
        arm_cells = [cells[(q, arm)] for q in qids if (q, arm) in cells]
        ok = [c for c in arm_cells if c.get("status") == "ok"]
        own = [c for qc in claims.values() for c in qc if c["arm_id"] == arm]
        verdicts = [support.get(c["id"], "uncited") if c.get("url") else "uncited" for c in own]
        supported, unsupported = verdicts.count(1), verdicts.count(0)
        uncited, unverifiable = verdicts.count("uncited"), verdicts.count(None)
        checkable = supported + unsupported + uncited
        cost = sum(float(c.get("cost", {}).get("usd") or 0.0) for c in arm_cells)
        weighted = sum(c["value"] for c in mine.values())
        latency = [float(c.get("latency_ms") or 0.0) for c in ok]
        per_arm[arm] = {
            "cells": len(arm_cells), "cells_ok": len(ok),
            "insights": len(mine), "weighted": weighted,
            "unique_insights": len(unique), "unique_weighted": sum(c["value"] for c in unique.values()),
            "share_of_pool": round(weighted / pooled, 4) if pooled else None,
            "claims": len(own), "supported": supported, "unsupported": unsupported,
            "uncited": uncited, "unverifiable": unverifiable,
            "unsupported_rate": round((unsupported + uncited) / checkable, 4) if checkable else None,
            "cost_usd": round(cost, 4),
            "cost_basis": sorted({c.get("cost", {}).get("basis", "none") for c in arm_cells}),
            "cost_per_insight": round(cost / len(mine), 4) if mine else None,
            "cost_per_unique_insight": round(cost / len(unique), 4) if unique else None,
            "latency_p50_ms": round(pct(latency, 0.5)), "latency_p90_ms": round(pct(latency, 0.9)),
            "questions_with_insight": sum(1 for q in qids if weighted_by_q[arm][q] > 0),
        }
    ranked = sorted(arms, key=lambda a: (-per_arm[a]["weighted"], per_arm[a]["cost_usd"], a))
    owned = {a: {cid for cid, c in insights.items() if a in c["credited"]} for a in arms}
    overlap = {a: {b: len(owned[a] & owned[b]) for b in arms} for a in arms}

    # Greedy marginal contribution: "if you could only buy k providers".
    greedy, covered, left = [], set(), list(arms)
    while left:
        def gain(a: str) -> int:
            return sum(insights[c]["value"] for c in owned[a] - covered)
        best = min(left, key=lambda a: (-gain(a), per_arm[a]["cost_usd"], a))
        added = gain(best)
        covered |= owned[best]
        left.remove(best)
        cumulative = sum(insights[c]["value"] for c in covered)
        greedy.append({"arm": best, "added_weighted": added, "cumulative_weighted": cumulative,
                       "cumulative_share": round(cumulative / pooled, 4) if pooled else None})

    winners = {}
    for q in qids:
        top = max((weighted_by_q[a][q] for a in arms), default=0)
        winners[q] = {"weighted": top,
                      "arms": sorted(a for a in arms if top and weighted_by_q[a][q] == top)}

    tests = []
    leader = ranked[0] if ranked else None
    for other in ranked[1:]:
        diffs = [weighted_by_q[leader][q] - weighted_by_q[other][q] for q in qids]
        wins, losses = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
        seed = int(hashlib.sha256(f"{grading['seed']}:{leader}:{other}".encode()).hexdigest()[:8], 16)
        lo, hi = bootstrap_ci(diffs, seed)
        tests.append({"leader": leader, "other": other, "n_questions": len(qids),
                      "ahead": wins, "behind": losses, "p_sign": round(sign_test(wins, losses), 4),
                      "ci95_total_diff": [lo, hi],
                      "leader_weighted": per_arm[leader]["weighted"],
                      "other_weighted": per_arm[other]["weighted"]})
    for test, adj in zip(tests, holm([t["p_sign"] for t in tests])):
        test["p_holm"] = round(adj, 4)

    profiles = {a: manifest["profiles"][a]["sha256"] for a in arms}
    models = {k: grading.get(k) for k in ("extractor", "judge")}
    return {
        "version": "insight-report-v1",
        "generated_at": now(),
        "run_id": manifest["run_id"], "set_id": manifest["set_id"],
        "set_sha256": manifest["set_sha256"], "run_date": manifest["run_date"],
        "seed": grading["seed"], "grading_models": models, "profiles": profiles,
        "comparable_key": hashlib.sha256(json.dumps(
            [manifest["set_sha256"], profiles, models], sort_keys=True).encode()).hexdigest()[:16],
        "arms": arms, "ranked": ranked, "questions": qids,
        "clusters": len(clusters), "insight_clusters": len(insights), "pooled_weighted": pooled,
        "per_arm": per_arm, "overlap": overlap, "greedy": greedy,
        "per_question": {q: {"winners": winners[q], "weighted": {a: weighted_by_q[a][q] for a in arms}}
                         for q in qids},
        "paired_tests": tests,
        "unverified_fields": {a: manifest["profiles"][a].get("unverified") or [] for a in arms
                              if manifest["profiles"][a].get("unverified")},
        "variants": [a for a in arms if manifest["profiles"][a]["profile"].get("variant")],
        "off_day_cells": sorted(f"{q}/{a}" for (q, a), c in cells.items()
                                if c.get("date") and c["date"] != manifest["run_date"]),
        "incomplete_cells": sorted(f"{q}/{a}" for q in qids for a in arms
                                   if cells.get((q, a), {}).get("status") != "ok"),
    }


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.4f}"


def _pctx(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _model(desc: Mapping[str, Any] | None) -> str:
    return f"{desc.get('harness')} / {desc.get('model')}" if desc else "not recorded"


def render(summary: Mapping[str, Any], history: Sequence[Mapping[str, Any]] = ()) -> str:
    arms, per = summary["ranked"], summary["per_arm"]
    n = len(summary["questions"])
    lines = [
        f"# Insight bake-off: {summary['set_id']} ({summary['run_date']})",
        "",
        f"Run `{summary['run_id']}`: {len(arms)} arms x {n} questions, same frozen profiles. "
        f"Grading seed {summary['seed']}; extractor {_model(summary['grading_models']['extractor'])}; "
        f"judge {_model(summary['grading_models']['judge'])}. Comparable key `{summary['comparable_key']}`.",
        "",
        "> Strict credit: an arm earns a finding only if its own cited page supports it. An insight is "
        "a credited finding valued 1-3 by a blind judge (3 decision-changing, 2 new design detail, "
        f"1 corroborating). Unique means no other arm earned it. Every figure below has n = {n} "
        "questions, one run, one day: treat small gaps as noise.",
        "",
        "## Arms",
        "",
        "| Arm | Insights | Weighted | Unique | Unique weighted | Share of pool | Cost | Cost per insight "
        "| Cost per unique | p50 latency | Unsupported rate | Cells ok |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for a in arms:
        s = per[a]
        cost = _money(s["cost_usd"]) + ("" if s["cost_basis"] == ["measured"] else " (est.)")
        lines.append(
            f"| {a} | {s['insights']} | {s['weighted']} | {s['unique_insights']} | {s['unique_weighted']} "
            f"| {_pctx(s['share_of_pool'])} | {cost} | {_money(s['cost_per_insight'])} "
            f"| {_money(s['cost_per_unique_insight'])} | {s['latency_p50_ms'] / 1000:.1f} s "
            f"| {_pctx(s['unsupported_rate'])} | {s['cells_ok']}/{s['cells']} |"
        )
    lines += [
        "",
        f"Pooled: {summary['insight_clusters']} insights of {summary['clusters']} claim clusters, "
        f"weighted {summary['pooled_weighted']}; shares overlap because a shared insight counts for "
        "every arm that earned it. Unsupported rate counts unsupported and uncited claims "
        "over checked ones; claims whose page could not be read are excluded and listed below.",
        "",
        "| Arm | Claims | Supported | Unsupported | Uncited | Unreadable page |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines += [f"| {a} | {per[a]['claims']} | {per[a]['supported']} | {per[a]['unsupported']} "
              f"| {per[a]['uncited']} | {per[a]['unverifiable']} |" for a in arms]
    lines += ["", "## If you could only buy k", "",
              "Greedy order by added weighted insight (ties to the cheaper arm).", "",
              "| k | Add | Added weighted | Cumulative | Share of pool |", "| ---: | --- | ---: | ---: | ---: |"]
    lines += [f"| {i + 1} | {g['arm']} | {g['added_weighted']} | {g['cumulative_weighted']} "
              f"| {_pctx(g['cumulative_share'])} |" for i, g in enumerate(summary["greedy"])]
    lines += ["", "## Overlap", "", "Insights both arms earned (diagonal: the arm's total).", "",
              "| | " + " | ".join(arms) + " |", "| --- |" + " ---: |" * len(arms)]
    lines += [f"| {a} | " + " | ".join(str(summary["overlap"][a][b]) for b in arms) + " |" for a in arms]
    lines += ["", "## Per question", "", "| Question | Top weighted | Winner(s) |", "| --- | ---: | --- |"]
    lines += [f"| {q} | {v['winners']['weighted']} | {', '.join(v['winners']['arms']) or 'none'} |"
              for q, v in summary["per_question"].items()]
    lines += ["", "## Is the lead real?", "",
              f"Leader against each arm on per-question weighted insight: exact two-sided sign test "
              f"(ties dropped), Holm-adjusted, and a seeded {BOOTSTRAP}-resample question bootstrap "
              "of the total difference. Tests were fixed by the bench, not chosen after the results.", "",
              "| Leader | Other | n questions | Ahead / behind | Sign p | Holm p | 95% CI of total difference |",
              "| --- | --- | ---: | --- | ---: | ---: | --- |"]
    lines += [f"| {t['leader']} | {t['other']} | {t['n_questions']} | {t['ahead']} / {t['behind']} "
              f"| {t['p_sign']:.3f} | {t['p_holm']:.3f} | {t['ci95_total_diff'][0]:g} to "
              f"{t['ci95_total_diff'][1]:g} |" for t in summary["paired_tests"]]
    if summary["variants"]:
        lines += ["", f"Pre-registered variants (report beside, not instead of, their primary arm): "
                  f"{', '.join(summary['variants'])}."]
    comparable = [h for h in history if h["comparable_key"] == summary["comparable_key"]]
    if len(comparable) > 1:
        lines += ["", "## Comparable runs", "",
                  "Same set, same profiles, same grading models (weighted insight per arm).", "",
                  "| Run | " + " | ".join(arms) + " |", "| --- |" + " ---: |" * len(arms)]
        lines += [f"| {h['run_id']} | " + " | ".join(str(h["weighted"].get(a, "-")) for a in arms) + " |"
                  for h in comparable]
    if summary["incomplete_cells"] or summary["off_day_cells"]:
        lines += ["", "## Cells to note", ""]
        if summary["incomplete_cells"]:
            lines.append(f"- Not ok (no claims, no credit): {', '.join(summary['incomplete_cells'])}")
        if summary["off_day_cells"]:
            lines.append(f"- Ran on a different day from the run date: {', '.join(summary['off_day_cells'])}")
    if summary["unverified_fields"]:
        lines += ["", "## Profile fields to confirm", "",
                  "Fields the profile research could not confirm from vendor docs. Check each against "
                  "this run's recorded requests and responses before quoting the arm.", "",
                  "| Arm | Unverified fields |", "| --- | --- |"]
        lines += [f"| {a} | {', '.join(f'`{f}`' for f in fields)} |"
                  for a, fields in summary["unverified_fields"].items()]
    return "\n".join(lines) + "\n"


def write_report(run_dir: Path) -> dict[str, Any]:
    summary = score(run_dir)
    history_path = run_dir.parent.parent / "history" / f"{summary['set_id']}.json"
    history = read_json(history_path) if history_path.is_file() else {"runs": {}}
    history["runs"][summary["run_id"]] = {
        "run_id": summary["run_id"], "run_date": summary["run_date"],
        "comparable_key": summary["comparable_key"], "set_sha256": summary["set_sha256"],
        "profiles": summary["profiles"], "reported_at": summary["generated_at"],
        "run_dir": str(run_dir),
        "weighted": {a: s["weighted"] for a, s in summary["per_arm"].items()},
        "unique": {a: s["unique_insights"] for a, s in summary["per_arm"].items()},
        "cost_usd": {a: s["cost_usd"] for a, s in summary["per_arm"].items()},
    }
    write_json(history_path, history)
    runs = sorted(history["runs"].values(), key=lambda h: (h["run_date"], h["run_id"]))
    write_json(run_dir / "summary.json", summary)
    (run_dir / "REPORT.md").write_text(render(summary, runs), encoding="utf-8")
    return {"report": str(run_dir / "REPORT.md"), "summary": str(run_dir / "summary.json"),
            "history": str(history_path), "ranked": summary["ranked"]}
