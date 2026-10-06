#!/usr/bin/env python3
"""Stage C: generate the results tables from data/stage-c (after unblinding; Amendment 12 adds the corrected tables).

Reads scores.json (as judged), scores_corrected.json (Amendment 12's corrected primary and sensitivities, from
score_c.py), claims.json and runs_meta.json, and fills the tables between <!-- c:<name>:start/end --> markers in the
results page. With the local runs/ folder present it first rebuilds runs_meta.json from it (no vendor text); without
it, it reads the committed runs_meta.json.

Usage:
  python3 make_tables_c.py [--data DIR] [results-stage-c.md]
  --data DIR  read the committed record in DIR (for example ../../data/stage-c) instead of ./data/stage-c
  With no page given, the tables are printed.
"""
import argparse, json, pathlib, re, sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from score_c import build_runs_meta  # noqa: E402  (one sanitizer for the run metadata)

SET = json.loads((ROOT / "sets" / "SV.json").read_text())
IDS = [i["id"] for i in SET["items"]]
PROV = ["exa", "parallel", "tavily", "firecrawl"]
NAMES = {"exa": "Exa", "parallel": "Parallel", "tavily": "Tavily", "firecrawl": "Firecrawl"}
VENDORS = {"google": "Google", "anthropic": "Anthropic", "openai": "OpenAI", "microsoft": "Microsoft"}
ARMS = [f"{p}:{a}" for a in ("search", "research") for p in PROV]
BASIS = {"exa": "billed", "parallel": "list price (estimate)", "tavily": "credits × $0.005–$0.008", "firecrawl": "credits × $0.00075–$0.005"}


def money(x):
    if x is None:
        return "—"
    if x >= 1:
        return f"${x:,.2f}"
    if x >= 0.1:
        return f"${x:.2f}"
    if x >= 0.01:
        return f"${x:.3f}"
    return f"${x:.4f}"


def span(lo, hi):
    if lo is None:
        return "—"
    return money(lo) if abs(lo - hi) < 1e-9 else f"{money(lo)}–{money(hi)}"


def arm_name(a):
    p, k = a.split(":")
    return f"{NAMES[p]} {k}"


def tables(S, V, CL, meta, T):
    P = V["primary"]
    t = {}
    # totals
    tj, tp = S["totals"], P["totals"]
    merged = tj["claims"] - tp["distinct"] - tp["bundles"] - tp["no_counted_run"]
    t["totals"] = (f"As judged, the judges wrote {tj['claims']} claims: {tj['new']} new to the KB ({tj['new_tier1']} of them tier 1), "
                   f"{tj['known']} already in the KB and {tj['false']} false. After Amendment 12's corrections they come to "
                   f"{tp['distinct']} distinct facts: {tp['new']} new ({tp['new_tier1']} of them tier 1), {tp['known']} known and "
                   f"{tp['false']} false. {merged} claims restate a fact another question wrote, {tp['bundles']} more only bundle "
                   f"facts written separately, and {tp['no_counted_run']} were stated only by Tavily's refused runs. Questions the "
                   f"judges marked answered on their tier-1 facet: {', '.join(tj['questions_answered'])}.")
    # providers (corrected primary)
    rows = ["| Provider | New tier-1 claims (either arm) | …found by no other provider | All new claims | Cost, both arms | Cost per new tier-1 claim |",
            "|---|---|---|---|---|---|"]
    for p in sorted(PROV, key=lambda p: (-P["providers"][p]["new_tier1"], PROV.index(p))):
        x = P["providers"][p]
        rows.append(f"| {NAMES[p]} | **{x['new_tier1']}** | {x['unique_new_tier1']} | {x['new_all']} | "
                    f"{span(x['cost_usd_low'], x['cost_usd_high'])} | {span(x['cost_per_new_tier1_low'], x['cost_per_new_tier1_high'])} |")
    t["providers"] = "\n".join(rows)
    # arms (corrected primary)
    rows = ["| Provider | Arm | New tier-1 claims | …found by no other arm | All new claims | Questions with a new claim (of 24) | Useful new tier-1 (2–3) | Research error rate | Median time |",
            "|---|---|---|---|---|---|---|---|---|"]
    for arm in ARMS:
        p, a = arm.split(":")
        x = P["arms"][arm]
        err = "—" if x["research_error_rate"] is None else f"{x['research_error_rate'] * 100:.1f}% ({x['research_errors']} of {x['research_checkable']})"
        rows.append(f"| {NAMES[p]} | {a} | **{x['new_tier1']}** | {x['unique_new_tier1']} | {x['new_all']} | {x['questions_with_new']} | "
                    f"{x['useful_new_tier1']} | {err} | {x['median_latency_s']} s |")
    t["arms"] = "\n".join(rows)
    # costs (corrected primary)
    rows = ["| Provider | Arm | Cost basis | Cost | Cost per new claim | Cost per new tier-1 claim |", "|---|---|---|---|---|---|"]
    for arm in ARMS:
        p, a = arm.split(":")
        x = P["arms"][arm]
        basis = BASIS[p] if p not in ("tavily", "firecrawl") else f"{x['credits']:,} {BASIS[p]}"
        rows.append(f"| {NAMES[p]} | {a} | {basis} | {span(x['cost_usd_low'], x['cost_usd_high'])} | "
                    f"{span(x['cost_per_new_low'], x['cost_per_new_high'])} | {span(x['cost_per_new_tier1_low'], x['cost_per_new_tier1_high'])} |")
    t["costs"] = "\n".join(rows)
    # variants
    rows = ["| Variant | Search: Exa / Par / Tav / Fir | Research: Exa / Par / Tav / Fir | Both arms (found by no other provider): Exa / Par / Tav / Fir | All new claims, both arms: Exa / Par / Tav / Fir |",
            "|---|---|---|---|---|"]
    for k, v in V.items():
        s = " / ".join(str(v["arms"][f"{p}:search"]["new_tier1"]) for p in PROV)
        r = " / ".join(str(v["arms"][f"{p}:research"]["new_tier1"]) for p in PROV)
        b = " / ".join(f"{v['providers'][p]['new_tier1']} ({v['providers'][p]['unique_new_tier1']})" for p in PROV)
        n = " / ".join(str(v["providers"][p]["new_all"]) for p in PROV)
        label = f"**{v['label']}**" if k == "primary" else v["label"]
        rows.append(f"| {label} | {s} | {r} | {b} | {n} |")
    t["variants"] = "\n".join(rows)
    # tests (corrected primary; post hoc)
    rows = ["| Arm | Leader | Against | New tier-1 (leader / other) | Questions ahead / behind | Sign test p | Holm-adjusted p (6 tests) | Bootstrap 95% CI of the difference |",
            "|---|---|---|---|---|---|---|---|"]
    for r in T["primary"]:
        rows.append(f"| {r['arm']} | {NAMES[r['leader']]} | {NAMES[r['other']]} | {r['leader_n']} / {r['other_n']} | {r['ahead']} / {r['behind']} | "
                    f"{r['p']:.3f} | {r['p_holm']:.3f} | {r['boot_lo']} to {r['boot_hi']} |")
    aj = min(T["as_judged"], key=lambda r: r["p_holm"])
    rows.append("")
    rows.append(f"As judged, the smallest of the six was {NAMES[aj['leader']]} against {NAMES[aj['other']]} in the {aj['arm']} arm: "
                f"p = {aj['p']:.3f} unadjusted, {aj['p_holm']:.3f} after Holm's adjustment.")
    t["tests"] = "\n".join(rows)
    # questions
    status = {x["question"]: x.get("status_of_question") for x in CL}
    byq = Counter(x["question"] for x in CL if x["verdict"] == "new" and x["tier"] == 1)
    rows = ["| Question | Vendor | Facet | Status (judged) | New tier-1, as judged | New tier-1, corrected | Arms that found one (corrected) |",
            "|---|---|---|---|---|---|---|"]
    for it in SET["items"]:
        q = it["id"]
        pq = P["questions"][q]
        rows.append(f"| {q} | {VENDORS[it['vendor']]} | {it['facet']} | {status.get(q, '—')} | {byq.get(q, 0)} | {pq['new_tier1']} | "
                    f"{', '.join(arm_name(a) for a in pq['arms']) or '—'} |")
    t["questions"] = "\n".join(rows)
    # how concentrated the finds are
    corr_q = {q: P["questions"][q]["new_tier1"] for q in IDS}
    top = max(IDS, key=lambda q: (corr_q[q], -IDS.index(q)))
    none_c = [q for q in IDS if corr_q[q] == 0]
    none_j = [q for q in IDS if byq.get(q, 0) == 0]
    t["lumpy"] = (f"The finds are lumpy. {top} carries {corr_q[top]} of the {tp['new_tier1']} corrected tier-1 facts, and "
                  f"{len(none_c)} of the 24 questions produced none ({len(none_j)} as judged: {', '.join(none_j)}).")
    # runs
    ok = Counter((m["provider"], m["arm"]) for m in meta if (m["arm"] == "search" and m["status"] == 200) or m["terminal_status"] == "completed")
    rows = ["| Provider | Search runs returned | Research runs completed | Research runs that returned nothing | Research runs the primary counts |",
            "|---|---|---|---|---|"]
    for p in PROV:
        done = {m["question"] for m in meta if m["provider"] == p and m["arm"] == "research" and m["terminal_status"] == "completed"}
        failed = sorted({m["question"] for m in meta if m["provider"] == p and m["arm"] == "research" and m["terminal_status"] == "failed"} - done)
        refused = sorted({m["question"] for m in meta if m["provider"] == p and m["arm"] == "research" and m["status"] == "skipped_cap"})
        counted = "24 of 24" if p != "tavily" else f"20 of 24 (o3, m2, m3, m6: refused by the cap, re-run under Amendment 10)"
        rows.append(f"| {NAMES[p]} | {ok[(p, 'search')]} of 24 | {ok[(p, 'research')]} of 24 | {', '.join(failed) or '—'} | {counted} |")
    t["runs"] = "\n".join(rows)
    # actual spend, every run (the ledgers' totals), at list value and across credit plans
    lo_hi = {"tavily": (0.005, 0.008), "firecrawl": (0.00075, 0.005)}
    list_usd = {"tavily": 0.008, "firecrawl": 0.005}
    tot = {"list": 0.0, "lo": 0.0, "hi": 0.0}
    arm = {}
    for m in meta:
        c = m["cost"]
        usd, cr = c.get("usd") or 0.0, c.get("credits", c.get("credits_billed")) or 0
        k = (m["provider"], m["arm"])
        a = arm.setdefault(k, [0.0, 0])
        a[0] += usd
        a[1] += cr
    for (p, _), (usd, cr) in arm.items():
        lo, hi = lo_hi.get(p, (0.0, 0.0))
        tot["list"] += usd + cr * list_usd.get(p, 0.0)
        tot["lo"] += usd + cr * lo
        tot["hi"] += usd + cr * hi
    search_list = sum(usd + cr * list_usd.get(p, 0.0) for (p, a), (usd, cr) in arm.items() if a == "search")
    tr = arm[("tavily", "research")][1]
    tr_counted = sum((m["cost"].get("credits") or 0) for m in meta if m["provider"] == "tavily" and m["arm"] == "research"
                     and m["question"] not in ("o3", "m2", "m3", "m6"))
    fr = arm[("firecrawl", "research")][1]
    t["spend"] = (f"- **Spend:** {money(tot['list'])} in total with credits at list value, against a $28 cap; "
                  f"{money(tot['lo'])}–{money(tot['hi'])} across credit plans.\n"
                  f"  - Search arm: {money(search_list)} at list value.\n"
                  f"  - Research arm: Exa {money(arm[('exa', 'research')][0])} (billed); Parallel {money(arm[('parallel', 'research')][0])} "
                  f"(list price); Tavily {tr:,} credits ({span(tr * 0.005, tr * 0.008)}), of which the primary counts {tr_counted:,}; "
                  f"Firecrawl {fr:,} credits ({span(fr * 0.00075, fr * 0.005)}).")
    return t


def fill(path, t):
    s = pathlib.Path(path).read_text()
    for k, v in t.items():
        s, n = re.subn(rf"(<!-- c:{k}:start -->\n)(?:.*?\n)?(<!-- c:{k}:end -->)", lambda m: m.group(1) + v + "\n" + m.group(2), s, flags=re.S)
        if n != 1:
            raise SystemExit(f"marker c:{k} not found once in {path}")
    pathlib.Path(path).write_text(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="the committed record, for example ../../data/stage-c")
    ap.add_argument("page", nargs="?")
    a = ap.parse_args()
    D = pathlib.Path(a.data).resolve() if a.data else ROOT / "data" / "stage-c"
    runs = ROOT / "runs"
    if not a.data and runs.exists():
        meta = build_runs_meta(runs)
        (D / "runs_meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    else:
        meta = json.loads((D / "runs_meta.json").read_text())
    S = json.loads((D / "scores.json").read_text())
    SC = json.loads((D / "scores_corrected.json").read_text())
    CL = json.loads((D / "claims.json").read_text())
    t = tables(S, SC["variants"], CL, meta, SC["tests"])
    if a.page:
        fill(a.page, t)
    else:
        for k, v in t.items():
            print(f"== {k}\n{v}\n")


if __name__ == "__main__":
    main()
