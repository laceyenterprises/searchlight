"""Score a graded insight run into a result bundle: summary.json, REPORT.md, dashboard.html.

Strict credit: an arm is credited with a cluster only when one of its own
claims in that cluster is supported by the page that claim cites. An insight is
a credited cluster the blind judge valued 1 or more; it is unique when exactly
one arm is credited. Everything here is offline arithmetic over run files.

Every metric and every claim in ``summary.json`` carries relative links to the
raw files it came from, written relative to ``<run>/report/``. The summary is
validated against ``config/insight/summary.schema.json`` before it is written,
and it is stable: re-reporting an unchanged run writes identical bytes.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from math import comb
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..catalog import module_root
from ..retrieval_lane import pct, wilson
from .catalog import InsightError, load_set
from .grade import load_claims, supported_claims
from .run import now, read_json, write_json

SCHEMA_VERSION = 2
SCHEMA_PATH = Path("config/insight/summary.schema.json")
BOOTSTRAP = 2000
LOCAL_ONLY = "local-only:"
METRICS = ("insights", "weighted", "unique_insights", "unique_weighted", "share_of_pool",
           "unsupported_rate", "cost_usd", "cost_per_insight", "cost_per_unique_insight",
           "latency_p50_ms")


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------


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


def bootstrap_ci(values: Sequence[float], seed: int, n: int = BOOTSTRAP) -> tuple[float, float]:
    """95% percentile interval of the total over a question resample."""
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    totals = sorted(sum(rng.choice(values) for _ in values) for _ in range(n))
    return (totals[int(0.025 * n)], totals[int(0.975 * n) - 1])


def _seed(*parts: Any) -> int:
    return int(hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()[:8], 16)


# --------------------------------------------------------------------------
# A tiny JSON Schema subset (type, enum, pattern, required, properties,
# additionalProperties, items, $ref, minimum): enough to pin the summary
# contract without a new dependency.
# --------------------------------------------------------------------------

_TYPES = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float),
          "boolean": bool, "null": type(None)}


def validate(doc: Any, schema: Mapping[str, Any], root: Mapping[str, Any] | None = None,
             where: str = "$") -> list[str]:
    root = root or schema
    if "$ref" in schema:
        schema = root["$defs"][schema["$ref"].rsplit("/", 1)[-1]]
    errors: list[str] = []
    types = schema.get("type")
    if types:
        types = [types] if isinstance(types, str) else types
        ok = any(isinstance(doc, _TYPES[t]) and not (t in ("integer", "number") and isinstance(doc, bool))
                 for t in types)
        if not ok:
            return [f"{where}: expected {'/'.join(types)}"]
    if "enum" in schema and doc not in schema["enum"]:
        errors.append(f"{where}: {doc!r} not in {schema['enum']}")
    if isinstance(doc, str) and "pattern" in schema and not re.search(schema["pattern"], doc):
        errors.append(f"{where}: {doc!r} does not match {schema['pattern']}")
    if isinstance(doc, (int, float)) and not isinstance(doc, bool) and doc < schema.get("minimum", doc):
        errors.append(f"{where}: below minimum")
    if isinstance(doc, dict):
        props = schema.get("properties", {})
        errors += [f"{where}: missing {k}" for k in schema.get("required", []) if k not in doc]
        extra = schema.get("additionalProperties", True)
        for key, value in doc.items():
            if key in props:
                errors += validate(value, props[key], root, f"{where}.{key}")
            elif extra is False:
                errors.append(f"{where}: unexpected {key}")
            elif isinstance(extra, Mapping):
                errors += validate(value, extra, root, f"{where}.{key}")
    if isinstance(doc, list) and "items" in schema:
        for i, item in enumerate(doc):
            errors += validate(item, schema["items"], root, f"{where}[{i}]")
    return errors


def load_schema() -> dict[str, Any]:
    return json.loads((module_root() / SCHEMA_PATH).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def _verify_key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:20]  # the key grade.py files pages under


def score(run_dir: Path, *, set_root: Path | None = None,
          history: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    manifest = read_json(run_dir / "manifest.json")
    grading = read_json(run_dir / "grading" / "grading.json")
    arms = sorted(manifest["profiles"])
    qids = list(manifest["questions"])
    try:
        iset = load_set(manifest["set_id"], set_root)
        set_doc = iset.raw if iset.sha256 == manifest["set_sha256"] else {}
    except InsightError:
        set_doc = {}
    texts = {q["id"]: q for q in set_doc.get("questions", [])}

    def link(*parts: str) -> str | None:
        return "../" + "/".join(parts) if (run_dir.joinpath(*parts)).is_file() else None

    def links(*items: str | None) -> list[str]:
        return sorted({i for i in items if i})

    cells = {}
    for path in (run_dir / "cells").glob("*/*.json"):
        doc = read_json(path)
        cells[(doc["question_id"], doc["arm_id"])] = doc
    claims = load_claims(run_dir)
    support = supported_claims(run_dir, claims)
    by_id = {(qid, c["id"]): c for qid, qc in claims.items() for c in qc}

    def verdict(c: Mapping[str, Any]) -> str:
        if not c.get("url"):
            return "uncited"
        return {1: "supported", 0: "unsupported", None: "unverifiable"}[support.get((c["question_id"], c["id"]))]

    def claim_view(c: Mapping[str, Any], qid: str) -> dict[str, Any]:
        key = _verify_key(c["url"]) if c.get("url") else None
        return {
            "id": c["id"], "arm": c["arm_id"], "text": c["text"], "url": c.get("url"),
            "verdict": verdict(c),
            "links": {"cell": link("cells", qid, f"{c['arm_id']}.json"),
                      "extract": link("grading", "extract", qid, f"{c['arm_id']}.json"),
                      "verify": link("grading", "verify", f"{key}.json") if key else None,
                      "page": link("grading", "pages", f"{key}.json") if key else None},
        }

    # Clusters per question, un-blinded: which arms found and which earned each one.
    questions, insights = [], {}
    for qid in qids:
        cpath, jpath = run_dir / "grading" / "clusters" / f"{qid}.json", run_dir / "grading" / "judge" / f"{qid}.json"
        values = read_json(jpath)["values"] if jpath.is_file() else {}
        views = []
        for cl in read_json(cpath)["clusters"] if cpath.is_file() else []:
            members = [by_id[(qid, m)] for m in cl["members"] if (qid, m) in by_id]
            credited = sorted({m["arm_id"] for m in members if support.get((qid, m["id"])) == 1})
            judged = values.get(cl["id"], {}) if credited else {}
            view = {"id": cl["id"], "value": judged.get("score"), "reason": judged.get("reason"),
                    "credited": credited, "found_by": sorted({m["arm_id"] for m in members}),
                    "unique": len(credited) == 1 and bool(judged.get("score")),
                    "claims": [claim_view(m, qid) for m in members]}
            views.append(view)
            if view["value"]:
                insights[cl["id"]] = {**view, "question_id": qid}
        views.sort(key=lambda v: (-(v["value"] or -1), v["id"]))
        questions.append({"id": qid, "text": texts.get(qid, {}).get("text", ""),
                          "freshness_days": texts.get(qid, {}).get("freshness_days"),
                          "clusters": views,
                          "links": {"clusters": link("grading", "clusters", f"{qid}.json"),
                                    "judge": link("grading", "judge", f"{qid}.json"),
                                    "query_writer": link("query-writer", f"{qid}.json")}})
    pooled = sum(c["value"] for c in insights.values())
    owned = {a: {cid for cid, c in insights.items() if a in c["credited"]} for a in arms}
    per_q = {a: {q: {"insights": 0, "weighted": 0, "unique_insights": 0, "unique_weighted": 0}
                 for q in qids} for a in arms}
    for c in insights.values():
        for a in c["credited"]:
            row = per_q[a][c["question_id"]]
            row["insights"] += 1
            row["weighted"] += c["value"]
            if c["unique"]:
                row["unique_insights"] += 1
                row["unique_weighted"] += c["value"]

    ledger_path = run_dir / "ledger.json"
    ledger = read_json(ledger_path) if ledger_path.is_file() else {"spent": {}}
    sponsor = set_doc.get("sponsor")
    arm_rows, metrics = [], {}
    for arm in arms:
        entry = manifest["profiles"][arm]
        profile, imported = entry.get("profile", {}), bool(entry.get("imported"))
        arm_cells = [(q, cells[(q, arm)]) for q in qids if (q, arm) in cells]
        ok = [c for _, c in arm_cells if c.get("status") == "ok"]
        own = [c for qc in claims.values() for c in qc if c["arm_id"] == arm]
        verdicts = [verdict(c) for c in own]
        counts = {k: verdicts.count(k) for k in ("supported", "unsupported", "uncited", "unverifiable")}
        counts.update(claims=len(own), cells=len(arm_cells), cells_ok=len(ok),
                      questions_with_insight=sum(1 for q in qids if per_q[arm][q]["weighted"]))
        checked = counts["supported"] + counts["unsupported"] + counts["uncited"]
        cost_known = not (imported and entry.get("cost_basis") == "unknown")
        cost = sum(float(c.get("cost", {}).get("usd") or 0.0) for _, c in arm_cells)
        latency = [float(c["latency_ms"]) for c in ok if c.get("latency_ms") is not None]
        cell_links = links(*(link("cells", q, f"{arm}.json") for q, _ in arm_cells),
                           link("ledger.json"))
        extract_links = [link("grading", "extract", q, f"{arm}.json") for q in qids]
        insight_links = links(*extract_links, *(link("grading", n, f"{q}.json")
                                                for q in qids for n in ("clusters", "judge")))
        claim_links = links(*extract_links, *(link("grading", "verify", f"{_verify_key(c['url'])}.json")
                                              for c in own if c.get("url")))

        def boot(name: str) -> dict[str, Any]:
            vector = [per_q[arm][q][name] for q in qids]
            lo, hi = bootstrap_ci(vector, _seed(grading["seed"], arm, name))
            return {"value": sum(vector), "n": len(qids), "ci95": [lo, hi], "links": insight_links}

        def plain(value: Any, n: int, source: list[str], ci: list[float] | None = None) -> dict[str, Any]:
            return {"value": value, "n": n, "ci95": ci, "links": source}

        m = {name: boot(name) for name in ("insights", "weighted", "unique_insights", "unique_weighted")}
        lo, hi = wilson(counts["unsupported"] + counts["uncited"], checked)
        m.update(
            share_of_pool=plain(round(m["weighted"]["value"] / pooled, 4) if pooled else None,
                                len(insights), insight_links),
            unsupported_rate=plain(round((counts["unsupported"] + counts["uncited"]) / checked, 4)
                                   if checked else None, checked, claim_links,
                                   [round(lo, 4), round(hi, 4)] if checked else None),
            cost_usd=plain(round(cost, 4) if cost_known else None, len(arm_cells), cell_links),
            cost_per_insight=plain(round(cost / m["insights"]["value"], 4)
                                   if cost_known and m["insights"]["value"] else None,
                                   m["insights"]["value"], cell_links + insight_links),
            cost_per_unique_insight=plain(round(cost / m["unique_insights"]["value"], 4)
                                          if cost_known and m["unique_insights"]["value"] else None,
                                          m["unique_insights"]["value"], cell_links + insight_links),
            latency_p50_ms=plain(round(pct(latency, 0.5)) if latency else None, len(latency), cell_links),
        )
        metrics[arm] = m
        docs = profile.get("docs") or entry.get("docs") or []
        arm_rows.append({
            "arm_id": arm, "vendor": profile.get("arm", arm.split(".")[0]),
            "operation": profile.get("operation", arm.split(".")[1]), "variant": profile.get("variant"),
            "kind": profile.get("kind") or ("results" if arm.split(".")[1] == "search" else "answer"),
            "imported": imported, "label": entry.get("label"), "note": entry.get("note"),
            "sponsor": bool(sponsor and profile.get("arm") in sponsor.get("products", [])),
            "profile_sha256": entry["sha256"],
            "docs": [d["url"] if isinstance(d, Mapping) else str(d) for d in docs],
            "unverified": list(entry.get("unverified") or []),
            "cost_basis": sorted({c.get("cost", {}).get("basis", "none") for _, c in arm_cells}),
            "counts": counts, "links": {"manifest": link("manifest.json")},
        })

    def cost_key(a: str) -> float:
        value = metrics[a]["cost_usd"]["value"]
        return float("inf") if value is None else value

    ranked = sorted(arms, key=lambda a: (-metrics[a]["weighted"]["value"], cost_key(a), a))
    greedy, covered, left = [], set(), list(arms)
    while left:
        def gain(a: str) -> int:
            return sum(insights[c]["value"] for c in owned[a] - covered)
        best = min(left, key=lambda a: (-gain(a), cost_key(a), a))
        added = gain(best)
        covered |= owned[best]
        left.remove(best)
        total = sum(insights[c]["value"] for c in covered)
        greedy.append({"arm": best, "added_weighted": added, "cumulative_weighted": total,
                       "cumulative_share": round(total / pooled, 4) if pooled else None})
    for q in questions:
        weights = {a: per_q[a][q["id"]]["weighted"] for a in arms}
        top = max(weights.values(), default=0)
        q.update(weighted=weights, top_weighted=top,
                 winners=sorted(a for a in arms if top and weights[a] == top))

    tests = []
    for other in ranked[1:]:
        leader = ranked[0]
        diffs = [per_q[leader][q]["weighted"] - per_q[other][q]["weighted"] for q in qids]
        wins, losses = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
        lo, hi = bootstrap_ci(diffs, _seed(grading["seed"], leader, other))
        tests.append({"leader": leader, "other": other, "n_questions": len(qids), "ahead": wins,
                      "behind": losses, "p_sign": round(sign_test(wins, losses), 4),
                      "ci95_total_diff": [lo, hi]})
    for test, adj in zip(tests, holm([t["p_sign"] for t in tests])):
        test["p_holm"] = round(adj, 4)

    shas = {a: manifest["profiles"][a]["sha256"] for a in arms}
    models = {k: grading.get(k) for k in ("extractor", "judge")}
    key = hashlib.sha256(json.dumps([manifest["set_sha256"], shas, models], sort_keys=True).encode()).hexdigest()[:16]
    imported = [{"arm_id": r["arm_id"], "label": r["label"], "note": r["note"],
                 "cost_basis": manifest["profiles"][r["arm_id"]].get("cost_basis"),
                 "reported_usd": manifest["profiles"][r["arm_id"]].get("reported_usd"),
                 "source_sha256": manifest["profiles"][r["arm_id"]].get("source_sha256")}
                for r in arm_rows if r["imported"]]
    off_day = sorted(f"{q}/{a}" for (q, a), c in cells.items() if c.get("date") and c["date"] != manifest["run_date"])
    incomplete = sorted(f"{q}/{a}" for q in qids for a in arms if cells.get((q, a), {}).get("status") != "ok")
    unverified = sum(len(r["unverified"]) for r in arm_rows)
    caveats = [f"One run on one day: every figure has n = {len(qids)} questions; treat small gaps as noise.",
               "The extractor and judges are models; verdicts and values were not checked by hand.",
               "Strict credit: an arm earns a finding only if its own cited page supports it."]
    if unverified:
        caveats.append(f"{unverified} profile fields are unverified against vendor docs; see the profile table.")
    if incomplete:
        caveats.append(f"{len(incomplete)} cells did not return ok and earn nothing.")
    if off_day:
        caveats.append(f"{len(off_day)} cells ran on a different day from the run date.")
    caveats += [f"{i['arm_id']} is imported: {i['note'] or 'produced outside this run'}." for i in imported]
    if sponsor:
        caveats.append(f"Sponsored by {sponsor['name']}: {sponsor['disclosure']}")
    return {
        "schema": "sew-insight-summary", "schema_version": SCHEMA_VERSION,
        "run": {"run_id": manifest["run_id"], "run_date": manifest["run_date"],
                "set_sha256": manifest["set_sha256"], "shared_sha256": manifest.get("shared_sha256"),
                "seed": grading["seed"], "graded_at": grading.get("updated_at"),
                "extractor": models["extractor"], "judge": models["judge"], "comparable_key": key,
                "links": {"manifest": link("manifest.json"), "ledger": link("ledger.json"),
                          "grading": link("grading", "grading.json")}},
        "set": {"set_id": manifest["set_id"], "title": set_doc.get("title") or manifest["set_id"],
                "context": set_doc.get("context_for_judges"), "questions": len(qids),
                "sponsor": sponsor},
        "arms": arm_rows, "ranked": ranked, "metrics": metrics,
        "pool": {"clusters": sum(len(q["clusters"]) for q in questions), "insights": len(insights),
                 "weighted": pooled},
        "overlap": {"arms": arms, "matrix": [[len(owned[a] & owned[b]) for b in arms] for a in arms]},
        "greedy": greedy, "questions": questions, "paired_tests": tests,
        "spend": {"by_arm": {a: round(float(ledger["spent"].get(a, 0.0)), 4) for a in arms},
                  "total_usd": round(sum(float(v) for v in ledger["spent"].values()), 4),
                  "max_usd": ledger.get("max_usd"), "links": links(link("ledger.json"))},
        "imported": imported, "caveats": caveats,
        "cells_to_note": {"incomplete": incomplete, "off_day": off_day},
        "comparable_runs": [h for h in history if h.get("comparable_key") == key],
    }


# --------------------------------------------------------------------------
# REPORT.md
# --------------------------------------------------------------------------


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.4f}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _ci(metric: Mapping[str, Any], fmt: Callable[[Any], str] = str) -> str:
    ci = metric.get("ci95")
    return f"{fmt(metric['value'])} ({fmt(ci[0])}-{fmt(ci[1])})" if ci and metric["value"] is not None \
        else fmt(metric["value"]) if metric["value"] is not None else "n/a"


def _cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def md_link(text: str, target: str | None) -> str:
    if not target:
        return "-"
    if target.startswith(LOCAL_ONLY):
        return f"{text} (local only)"
    return f"[{text}]({target.replace(' ', '%20')})"


def _model(desc: Mapping[str, Any] | None) -> str:
    return f"{desc.get('harness')} / {desc.get('model')}" if desc else "not recorded"


def render(summary: Mapping[str, Any], *, base: str = "") -> str:
    """The human report. ``base`` prefixes the bundle's own files (summary, dashboard)."""
    run, metrics, n = summary["run"], summary["metrics"], summary["set"]["questions"]
    arms = {a["arm_id"]: a for a in summary["arms"]}

    def name(arm: str) -> str:
        tags = [t for t, on in (("imported", arms[arm]["imported"]), ("variant", arms[arm]["variant"]),
                                ("sponsor", arms[arm]["sponsor"])) if on]
        return arm + (f" ({', '.join(tags)})" if tags else "")

    lines = [f"# Insight bake-off: {summary['set']['title']} ({run['run_date']})", "",
             f"Run `{run['run_id']}`: {len(arms)} arms x {n} questions. Extractor {_model(run['extractor'])}; "
             f"judge {_model(run['judge'])}; seed {run['seed']}; comparable key `{run['comparable_key']}`. "
             f"{md_link('Interactive dashboard', base + 'dashboard.html')} | "
             f"{md_link('summary.json', base + 'summary.json')}", ""]
    if summary["set"]["sponsor"]:
        s = summary["set"]["sponsor"]
        lines += [f"> **Sponsor disclosure.** {s['name']} sponsored this study. {s['disclosure']}", ""]
    lines += ["## Read this first", ""] + [f"- {c}" for c in summary["caveats"]] + [
        "", "## Arms", "",
        "Insight counts and weighted scores carry 95% question-bootstrap intervals; the unsupported rate "
        "carries a Wilson interval.", "",
        "| Arm | Insights | Weighted | Unique | Share | Cost | Per insight | Per unique | p50 latency "
        "| Unsupported | Cells ok |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for arm in summary["ranked"]:
        m, row = metrics[arm], arms[arm]
        latency = m["latency_p50_ms"]["value"]
        lines.append(
            f"| {name(arm)} | {_ci(m['insights'])} | {_ci(m['weighted'])} | {_ci(m['unique_insights'])} "
            f"| {_pct(m['share_of_pool']['value'])} | {_money(m['cost_usd']['value'])} "
            f"| {_money(m['cost_per_insight']['value'])} | {_money(m['cost_per_unique_insight']['value'])} "
            f"| {'n/a' if latency is None else f'{latency / 1000:.1f} s'} "
            f"| {_ci(m['unsupported_rate'], _pct)} | {row['counts']['cells_ok']}/{row['counts']['cells']} |")
    pool = summary["pool"]
    lines += ["", f"Pooled: {pool['insights']} insights of {pool['clusters']} claim clusters, weighted "
              f"{pool['weighted']}. Shares overlap, because a shared insight counts for every arm that earned it.", ""]
    if summary["imported"]:
        lines += ["**Imported arms** were produced outside this run. Their claims were re-extracted and "
                  "verified like every other arm's, but they did not run under the run's profiles, date or ledger.", ""]
        lines += [f"- `{i['arm_id']}` (imported): {i['label']}. Caveat: {i['note'] or 'none recorded'}. "
                  f"Cost {'reported' if i['cost_basis'] == 'reported' else 'unknown'}; source sha256 "
                  f"`{(i['source_sha256'] or '')[:16]}`." for i in summary["imported"]] + [""]
    lines += ["## If you could only buy k", "", "| k | Add | Added weighted | Cumulative | Share |",
              "| ---: | --- | ---: | ---: | ---: |"]
    lines += [f"| {i + 1} | {g['arm']} | {g['added_weighted']} | {g['cumulative_weighted']} "
              f"| {_pct(g['cumulative_share'])} |" for i, g in enumerate(summary["greedy"])]
    ov = summary["overlap"]
    lines += ["", "## Overlap", "", "Insights both arms earned; the diagonal is the arm's total.", "",
              "| | " + " | ".join(ov["arms"]) + " |", "| --- |" + " ---: |" * len(ov["arms"])]
    lines += [f"| {a} | " + " | ".join(map(str, row)) + " |" for a, row in zip(ov["arms"], ov["matrix"])]
    lines += ["", "## Unique insights", "", "What exactly one arm found and its own page supported.", "",
              "| Value | Arm | Question | Finding | Source | Raw |", "| ---: | --- | --- | --- | --- | --- |"]
    for q in summary["questions"]:
        for cl in q["clusters"]:
            if cl["unique"]:
                claim = next(c for c in cl["claims"] if c["arm"] == cl["credited"][0] and c["verdict"] == "supported")
                lines.append(f"| {cl['value']} | {cl['credited'][0]} | {q['id']} | {_cell(claim['text'])} "
                             f"| {md_link('page', claim['url'])} | {md_link('cell', claim['links']['cell'])}, "
                             f"{md_link('verify', claim['links']['verify'])}, {md_link('captured page', claim['links']['page'])} |")
    lines += ["", "## Per question", "", "| Question | Top weighted | Winner(s) | Insights | Raw |",
              "| --- | ---: | --- | ---: | --- |"]
    for q in summary["questions"]:
        raw = ", ".join(md_link(k.replace("_", "-"), v) for k, v in q["links"].items() if v) or "-"
        lines.append(f"| {q['id']} | {q['top_weighted']} | {', '.join(q['winners']) or 'none'} "
                     f"| {sum(1 for c in q['clusters'] if c['value'])} | {raw} |")
    lines += ["", "## Is the lead real?", "",
              f"Leader against each arm on per-question weighted insight: exact two-sided sign test (ties dropped), "
              f"Holm-adjusted, and a seeded {BOOTSTRAP}-resample question bootstrap of the total difference.", "",
              "| Leader | Other | n | Ahead / behind | Sign p | Holm p | 95% CI of difference |",
              "| --- | --- | ---: | --- | ---: | ---: | --- |"]
    lines += [f"| {t['leader']} | {t['other']} | {t['n_questions']} | {t['ahead']} / {t['behind']} | {t['p_sign']:.3f} "
              f"| {t['p_holm']:.3f} | {t['ci95_total_diff'][0]:g} to {t['ci95_total_diff'][1]:g} |"
              for t in summary["paired_tests"]]
    lines += ["", "## Profiles", "", "| Arm | Profile sha256 | Docs | Unverified fields |", "| --- | --- | --- | --- |"]
    for arm in summary["ranked"]:
        row = arms[arm]
        docs = ", ".join(md_link(str(i + 1), d) for i, d in enumerate(row["docs"][:3])) or "-"
        lines.append(f"| {name(arm)} | `{row['profile_sha256'][:12]}` | {docs} "
                     f"| {', '.join(f'`{f}`' for f in row['unverified']) or '-'} |")
    if summary["comparable_runs"][1:]:
        lines += ["", "## Comparable runs", "", "| Run | " + " | ".join(summary["ranked"]) + " |",
                  "| --- |" + " ---: |" * len(summary["ranked"])]
        lines += [f"| {h['run_id']} | " + " | ".join(str(h["weighted"].get(a, "-")) for a in summary["ranked"]) + " |"
                  for h in summary["comparable_runs"]]
    spend = summary["spend"]
    lines += ["", "## Raw data", "",
              f"- Run manifest: {md_link('manifest.json', run['links']['manifest'])}; spend ledger: "
              f"{md_link('ledger.json', run['links']['ledger'])} (${spend['total_usd']:,.4f} recorded); "
              f"grading record: {md_link('grading.json', run['links']['grading'])}.",
              "- Every metric and claim in summary.json links to its cell, extract, cluster, verify and "
              "value files; the dashboard's Files view lists them all."]
    notes = summary["cells_to_note"]
    if notes["incomplete"]:
        lines.append(f"- Not ok (no claims, no credit): {', '.join(notes['incomplete'])}")
    if notes["off_day"]:
        lines.append(f"- Ran on a different day from the run date: {', '.join(notes['off_day'])}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# The bundle
# --------------------------------------------------------------------------


def write_bundle(directory: Path, summary: Mapping[str, Any], *, base: str = "") -> dict[str, str]:
    from .dashboard import render_dashboard

    errors = validate(summary, load_schema())
    if errors:
        raise InsightError("summary.json fails its schema: " + "; ".join(errors[:5]))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                                            encoding="utf-8")
    (directory / "REPORT.md").write_text(render(summary, base=base), encoding="utf-8")
    (directory / "dashboard.html").write_text(render_dashboard(summary), encoding="utf-8")
    return {k: str(directory / f) for k, f in (("summary", "summary.json"), ("report", "REPORT.md"),
                                                ("dashboard", "dashboard.html"))}


def write_report(run_dir: Path, *, set_root: Path | None = None) -> dict[str, Any]:
    """Score the run into ``<run>/report/`` and record it in the set's run history."""
    manifest = read_json(run_dir / "manifest.json")
    history_path = run_dir.parent.parent / "history" / f"{manifest['set_id']}.json"
    history = read_json(history_path) if history_path.is_file() else {"runs": {}}
    first = score(run_dir, set_root=set_root)
    history["runs"][manifest["run_id"]] = {
        "run_id": manifest["run_id"], "run_date": manifest["run_date"],
        "comparable_key": first["run"]["comparable_key"],
        "weighted": {a: m["weighted"]["value"] for a, m in first["metrics"].items()},
        "unique": {a: m["unique_insights"]["value"] for a, m in first["metrics"].items()},
        "cost_usd": {a: m["cost_usd"]["value"] for a, m in first["metrics"].items()},
    }
    write_json(history_path, history)
    runs = sorted(history["runs"].values(), key=lambda h: (h["run_date"], h["run_id"]))
    summary = score(run_dir, set_root=set_root, history=runs)
    paths = write_bundle(run_dir / "report", summary)
    return {**paths, "history": str(history_path), "ranked": summary["ranked"], "reported_at": now()}
