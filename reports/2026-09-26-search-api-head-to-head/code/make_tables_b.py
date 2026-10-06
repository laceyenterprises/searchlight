#!/usr/bin/env python3
"""Generate Stage B's hard-number tables from the run records and the blind judges' verdicts, so the write-up can't drift.

Reads runs/ (S5, S6, SP, and SM records, and both ledgers) and grading/b/ (pools, attribution, judge verdicts, the
post-hoc files of Amendments 6 and 7, and scored_b.json from score_b.py). Writes grading/b/tables_stage_b.json, and
replaces the block between <!-- tables:<name>:start --> and <!-- tables:<name>:end --> markers in any markdown file
passed on the command line.

Usage: python3 make_tables_b.py [--data DIR] [results-stage-b.md README.md ...]
  --data DIR  read runs/ and grading/ from DIR (for example ../data/stage-b) instead of this folder. The Stage A
              ledger is read from DIR/runs/ledger.json, or else from ../stage-a/runs/ledger.json next to DIR.
"""
import collections, json, pathlib, re, statistics, sys
from math import comb
from urllib.parse import urlsplit

args = sys.argv[1:]
DATA = pathlib.Path(__file__).resolve().parent
if "--data" in args:
    i = args.index("--data")
    DATA = pathlib.Path(args[i + 1]).resolve()
    args = args[:i] + args[i + 2:]
B = DATA / "grading" / "b"
P = ["exa", "parallel", "firecrawl"]
NAME = {"exa": "Exa", "parallel": "Parallel", "firecrawl": "Firecrawl"}
NAME_R = {v: k for k, v in NAME.items()}
TAV = 0.008                 # $ per Tavily credit (PAYG), for the Stage A total
FC_LO, FC_HI = 0.00075, 0.005   # $ per Firecrawl credit: Scale plan to Hobby top-up. The cap guard used FC_HI.
SOCIAL = ("x.com", "twitter.com", "linkedin.com", "instagram.com", "facebook.com")
HDR = "| | " + " | ".join(NAME[p] for p in P) + " |\n|---|" + "---|" * len(P)
FIELD = {"soc2": "SOC 2", "hipaa_baa": "HIPAA BAA", "zero_data_retention": "Zero data retention",
         "official_mcp_server": "Official MCP server", "free_tier": "Free tier", "last_funding": "Last funding",
         "list_price": "List price", "web_search_source": "Web search source"}
CTYPE = {"substantive": "substantive", "detail": "detail", "contested": "contested", "vendor_claim_only": "vendor claim only",
         "vendor_pages_conflict": "vendor pages conflict"}


def row(label, vals, fmt=str):
    return "| " + label + " | " + " | ".join(fmt(vals[p]) if p in vals else "—" for p in P) + " |"


def recs(set_name):
    out = []
    for p in P:
        f = DATA / "runs" / set_name / f"{p}.jsonl"
        if f.exists():
            out += [json.loads(line) for line in open(f)]
    return out


def last(set_name):
    return {(r["provider"], r["id"]): r for r in recs(set_name) if r.get("status") != "skipped_cap"}


def reg(u):
    h = (urlsplit(u if "://" in u else "https://" + u).hostname or "").lower()
    h = h[4:] if h.startswith("www.") else h
    return ".".join(h.split(".")[-2:])


def src(u):
    if not u:
        return "—"
    d = reg(u)
    if d in SOCIAL or "[redacted-" in u:
        return f"{d} post (URL in the grading data)"
    return f"[{d}]({u})"


def pct(a, b):
    return f"{a}/{b} ({100 * a / b:.1f}%)" if b else "—"


def trim(t, n=230):
    t = (t or "").replace("|", "/").replace("\n", " ").strip()
    return t if len(t) <= n else t[:n - 3].rsplit(" ", 1)[0] + "…"


def fmt_p(q):
    """p-value with its operator: '= 0.23', '= 0.003', or '< 0.001'."""
    return f"= {q:.2f}" if q >= 0.01 else ("< 0.001" if q < 0.001 else f"= {q:.3f}")


def sign_p(a, b):
    """Exact two-sided sign test on discordant pairs."""
    n, k = a + b, min(a, b)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0


def fisher_p(a, b, c, d):
    """Two-sided Fisher exact test on [[a, b], [c, d]] (sum of tables no likelier than the observed one)."""
    r1, c1, n = a + b, a + c, a + b + c + d
    pr = lambda x: comb(c1, x) * comb(n - c1, r1 - x) / comb(n, r1)
    p0 = pr(a)
    return min(1.0, sum(pr(x) for x in range(max(0, r1 - (n - c1)), min(r1, c1) + 1) if pr(x) <= p0 * (1 + 1e-9)))


def s5_judge(lid):
    j = json.loads((B / f"S5_{lid}_judge.json").read_text())
    return next(x for x in j if x.get("list") == lid) if isinstance(j, list) else j


def fc_money(credits):
    return f"${credits * FC_LO:.2f}–${credits * FC_HI:.2f}"


def main():
    T, J = {}, {}
    sc = json.loads((B / "scored_b.json").read_text())
    led = json.loads((DATA / "runs" / "ledger_b.json").read_text())["spent"]
    la_path = next((p for p in [DATA / "runs" / "ledger.json", DATA.parent / "stage-a" / "runs" / "ledger.json"] if p.exists()), None)
    la = json.loads(la_path.read_text()) if la_path else None

    # ---- cost ----
    r5, r6, rp = last("S5"), last("S6"), last("SP")
    usd = lambda rs, p: sum((r.get("cost") or {}).get("usd") or 0 for (q, _), r in rs.items() if q == p)
    n_runs = lambda rs, p: sum(1 for (q, _) in rs if q == p)
    fc = {k: [r for (q, _), r in rs.items() if q == "firecrawl"] for k, rs in (("S5", r5), ("S6", r6))}
    fc_cred = {k: sum((r.get("cost") or {}).get("credits_billed") or 0 for r in v) for k, v in fc.items()}
    fc_paid = {k: sum(1 for r in v if (r.get("cost") or {}).get("paid_run")) for k, v in fc.items()}
    skipped = [r for r in recs("S5") if r.get("status") == "skipped_cap"]
    T["b_cost"] = "\n".join([HDR,
        row("S5 entity lists (3 lists, one run each)", {"exa": f"${usd(r5, 'exa'):.2f}", "parallel": f"${usd(r5, 'parallel'):.2f} (list price)",
            "firecrawl": f"{fc_cred['S5']} credits ({len(fc['S5']) - fc_paid['S5']} free daily runs)"}),
        row("S6 schema fill (20 companies × 8 fields)", {"exa": f"${usd(r6, 'exa'):.2f} ({n_runs(r6, 'exa')} runs)", "parallel": f"${usd(r6, 'parallel'):.2f} ({n_runs(r6, 'parallel')} runs, list price)",
            "firecrawl": f"{fc_cred['S6']} credits ({fc_money(fc_cred['S6'])} by plan): {fc_paid['S6']} paid runs of 5 companies, {len(fc['S6']) - fc_paid['S6']} free"}),
        row("SP find-similar (10 seeds)", {"exa": f"${usd(rp, 'exa'):.2f} ({n_runs(rp, 'exa')} calls)"}),
        row("SM Monitors (5, daily to 2026-10-10)", {"exa": "$15 per 1,000 runs; at most 70 runs ($1.05); billed at the end"}),
        row("Stage B total (ledger)", {"exa": f"${led['exa']['usd']:.2f}", "parallel": f"${led['parallel']['usd']:.2f}",
            "firecrawl": f"{led['firecrawl']['credits']} credits ({fc_money(led['firecrawl']['credits'])} by plan)"})])
    b_total = led["exa"]["usd"] + led["parallel"]["usd"] + led["firecrawl"]["credits"] * FC_HI
    J["cost"] = {"ledger_b": led, "firecrawl_credits": fc_cred, "firecrawl_paid_runs": fc_paid, "stage_b_usd_max": round(b_total, 2)}
    if la:
        a_total = la["exa"]["usd"] + la["parallel"]["usd"] + la["tavily"]["credits"] * TAV + la["firecrawl"]["credits"] * FC_HI
        J["cost"]["stage_a_usd_max"] = round(a_total, 2)
        T["b_total"] = (f"At most ${b_total:.2f} for Stage B and ${a_total:.2f} for Stage A, or ${a_total + b_total:.2f} of the owner's $40 cap. "
                        f"This values credits at the most they cost (Tavily ${TAV}, Firecrawl ${FC_HI}), as the cap guard did; at Firecrawl's "
                        f"Scale rate the total is ${a_total + b_total - (led['firecrawl']['credits'] + la['firecrawl']['credits']) * (FC_HI - FC_LO):.2f}. "
                        f"The Monitors probe adds at most $1.05 by 2026-10-10. The guard refused {len(skipped)} run, Parallel's S5c, before the cap correction; it ran after.")

    # ---- latency (poll-quantized: Exa polls every 12 s, Parallel 15 s, Firecrawl 35 s) ----
    lat = lambda rs, p: sorted(r["latency_ms"] / 1000 for (q, _), r in rs.items() if q == p and r.get("latency_ms"))
    rng = lambda v: f"{min(v):.0f}–{max(v):.0f} s" if v else "—"
    fcl = lat(r6, "firecrawl")
    T["b_latency"] = "\n".join([HDR,
        row("S5, one list (range over 3 runs)", {p: rng(lat(r5, p)) for p in P}),
        row("S6, per company (median; range)", {"exa": f"{statistics.median(lat(r6, 'exa')):.0f} s ({rng(lat(r6, 'exa'))})",
            "parallel": f"{statistics.median(lat(r6, 'parallel')):.0f} s ({rng(lat(r6, 'parallel'))})",
            "firecrawl": f"{min(fcl) / 5:.0f}–{max(fcl) / 5:.0f} s (runs of five companies took {rng(fcl)})"}),
        row("SP, one find-similar call (median)", {"exa": f"{statistics.median(lat(rp, 'exa')) * 1000:,.0f} ms"})])

    # ---- S5 ----
    meta5 = json.loads((B / "S5_meta.json").read_text())
    urls5 = json.loads((B / "S5_url_completeness.json").read_text())["lists"] if (B / "S5_url_completeness.json").exists() else {}
    s5, tot = sc["S5"], collections.defaultdict(collections.Counter)
    keys = ("returned", "meets", "partial", "fails", "unverifiable", "verified_novel", "verified_novel_unique", "returned_excluded",
            "verified_novel_as_judged", "verified_novel_unique_as_judged", "verified_novel_entity_level", "verified_novel_unique_entity_level")
    for lid in ["S5a", "S5b", "S5c"]:
        d = s5[lid]
        for p in P:
            for k in keys:
                tot[p][k] += d[p].get(k, 0)
            tot[p]["raw"] += meta5[lid]["returned"].get(p, 0)
            tot[p]["with_url"] += urls5.get(lid, {}).get(p, {}).get("with_url", 0)
        T[f"s5_{lid}"] = "\n".join([HDR,
            row("Entities returned (distinct by domain)", {p: (f"{d[p].get('returned', 0)}" + (f" (of {meta5[lid]['returned'][p]} raw)" if meta5[lid]["returned"].get(p, 0) != d[p].get("returned", 0) else "")) for p in P}),
            row("… with a homepage URL (raw)", {p: (lambda u: f"{u['with_url']}/{u['returned_raw']}" if u else "—")(urls5.get(lid, {}).get(p)) for p in P}),
            row("Meet every criterion (precision)", {p: pct(d[p].get("meets", 0), d[p].get("returned", 0)) for p in P}),
            row("Partial / fail / unverifiable", {p: f"{d[p].get('partial', 0)} / {d[p].get('fails', 0)} / {d[p].get('unverifiable', 0)}" for p in P}),
            row("Returned despite the exclusion list", {p: d[p].get("returned_excluded", 0) for p in P}),
            row("Verified and new to the KB (fact-level rule)", {p: d[p].get("verified_novel", 0) for p in P}),
            row("… no other provider returned it", {p: d[p].get("verified_novel_unique", 0) for p in P})])
        T[f"s5_{lid}_pool"] = (f"Pooled: {d['pooled_after_merge']} distinct entities" + (f" ({d['pooled']} before the post-hoc merge)" if d["pooled"] != d["pooled_after_merge"] else "")
                               + f"; {d['verified_novel_any']} verified and new to the KB from any provider (fact-level rule; "
                               + f"{d['verified_novel_any_as_judged']} as judged, {d['verified_novel_any_entity_level']} under the entity-level rule).")
    spend5 = {p: usd(r5, p) for p in P}
    per_new = lambda p, k: (lambda x: f"${x:.2f}" if x >= 1 else f"${x:.3f}")(spend5[p] / tot[p][k]) if tot[p][k] else "—"
    T["s5_total"] = "\n".join([HDR,
        row("Entities returned, three lists (distinct by domain)", {p: f"{tot[p]['returned']}" + (f" (of {tot[p]['raw']} raw)" if tot[p]["raw"] != tot[p]["returned"] else "") for p in P}),
        row("… with a homepage URL (raw)", {p: f"{tot[p]['with_url']}/{tot[p]['raw']}" for p in P}),
        row("Meet every criterion (precision)", {p: pct(tot[p]["meets"], tot[p]["returned"]) for p in P}),
        row("Returned despite the exclusion list", {p: tot[p]["returned_excluded"] for p in P}),
        row("Verified and new to the KB (fact-level rule)", {p: tot[p]["verified_novel"] for p in P}),
        row("… no other provider returned it", {p: tot[p]["verified_novel_unique"] for p in P}),
        row("S5 spend", {"exa": f"${spend5['exa']:.2f}", "parallel": f"${spend5['parallel']:.2f} (list price)", "firecrawl": f"{fc_cred['S5']} credits (free runs)"}),
        row("Spend per verified new entity", {"exa": per_new("exa", "verified_novel"), "parallel": per_new("parallel", "verified_novel"),
            "firecrawl": "not measurable (free runs)"})])
    T["s5_rules"] = "\n".join(["| Novelty rule | " + " | ".join(NAME[p] for p in P) + " | Any provider |", "|---|" + "---|" * (len(P) + 1),
        "| Fact-level (primary): the KB's leaves don't record the fact the list asks for | " + " | ".join(f"{tot[p]['verified_novel']} ({tot[p]['verified_novel_unique']} unique)" for p in P) + f" | {sum(s5[l]['verified_novel_any'] for l in ['S5a', 'S5b', 'S5c'])} |",
        "| As judged: S5b judged entity-level, S5c fact-level | " + " | ".join(f"{tot[p]['verified_novel_as_judged']} ({tot[p]['verified_novel_unique_as_judged']} unique)" for p in P) + f" | {sum(s5[l]['verified_novel_any_as_judged'] for l in ['S5a', 'S5b', 'S5c'])} |",
        "| Entity-level: the company appears nowhere in the KB's leaves | " + " | ".join(f"{tot[p]['verified_novel_entity_level']} ({tot[p]['verified_novel_unique_entity_level']} unique)" for p in P) + f" | {sum(s5[l]['verified_novel_any_entity_level'] for l in ['S5a', 'S5b', 'S5c'])} |",
        "| Spend per new entity, fact-level (entity-level) | " + " | ".join((f"{per_new(p, 'verified_novel')} ({per_new(p, 'verified_novel_entity_level')})" if p != "firecrawl" else "free runs") for p in P) + " | — |"])
    any_new = sum(s5[l]["verified_novel_any"] for l in ["S5a", "S5b", "S5c"])
    pooled = sum(s5[l]["pooled_after_merge"] for l in ["S5a", "S5b", "S5c"])
    T["s5_any"] = (f"Across the three lists the pool held {pooled} distinct entities, and {any_new} of them were verified and new to the KB "
                   f"(fact-level rule; {sum(s5[l]['verified_novel_any_as_judged'] for l in ['S5a', 'S5b', 'S5c'])} as judged, "
                   f"{sum(s5[l]['verified_novel_any_entity_level'] for l in ['S5a', 'S5b', 'S5c'])} under the stricter entity-level rule).")
    J["S5"] = {"totals": {p: dict(tot[p]) for p in P}, "pooled": pooled, "verified_new_any": any_new, "spend": spend5}

    # S5 claimed-field checks, on entities only one provider returned (so the verdict is that provider's)
    fields = collections.defaultdict(collections.Counter)
    for lid in ["S5a", "S5b", "S5c"]:
        at = json.loads((B / "_attrib" / f"S5_{lid}_attribution.json").read_text())
        for e in s5_judge(lid)["entities"]:
            ps = at.get(e["eid"], [])
            if len(ps) != 1:
                continue
            for f, v in (e.get("fields") or {}).items():
                fields[(lid, f)][(ps[0], v)] += 1
    lines = ["| List | Field | " + " | ".join(NAME[p] for p in P) + " |", "|---|---|" + "---|" * len(P)]
    for (lid, f), c in sorted(fields.items()):
        cell = lambda p: (lambda ok, n: f"{ok}/{n}" if n else "—")(c[(p, "correct")], sum(v for (q, _), v in c.items() if q == p))
        lines.append(f"| {lid} | `{f}` | " + " | ".join(cell(p) for p in P) + " |")
    T["s5_fields"] = "\n".join(lines)

    # S5 findings: every verified new entity (fact-level), deduplicated after the merge
    ph = json.loads((B / "S5_posthoc.json").read_text()) if (B / "S5_posthoc.json").exists() else {}
    MERGE_DUP = {"S5b-e6": "S5b-e2", "S5c-e26": "S5c-e13"}
    rows = ["| List | Entity | Domain | Found by | What the judge verified (paraphrase) |", "|---|---|---|---|---|"]
    fb = []
    for lid in ["S5a", "S5b", "S5c"]:
        pool = {e["eid"]: e for e in json.loads((B / f"S5_{lid}_pool.json").read_text())["entities"]}
        at = json.loads((B / "_attrib" / f"S5_{lid}_attribution.json").read_text())
        for e in s5_judge(lid)["entities"]:
            new = e.get("novelty") == "novel" or e["eid"] in ph.get("fact_level_to_novel", {})
            if e["overall"] != "meets" or not new or e["eid"] in MERGE_DUP:
                continue
            finders = set(at.get(e["eid"], [])) | {p for dup, keep in MERGE_DUP.items() if keep == e["eid"] for p in at.get(dup, [])}
            fb.append(finders)
            pe = pool[e["eid"]]
            dom = pe["key"] if not pe["key"].startswith("name:") else "—"
            note = trim(e.get("note"), 200)
            if e["eid"] in ph.get("fact_level_to_novel", {}):
                note = "Judged known; new under the fact-level rule (Amendment 7): " + trim(ph["fact_level_to_novel"][e["eid"]], 150)
            if e["eid"] in ph.get("entity_level_to_known", {}):
                note += " Known under the entity-level rule: " + trim(ph["entity_level_to_known"][e["eid"]], 100)
            rows.append(f"| {lid} | {trim(pe['names'][0], 60)} | {dom} | {', '.join(NAME[p] for p in P if p in finders)} | {note} |")
    T["findings_s5"] = "\n".join(rows)
    only = {p: sum(1 for f in fb if f == {p}) for p in P}
    T["s5_union"] = (f"Exa and Firecrawl together found {sum(1 for f in fb if f & {'exa', 'firecrawl'})} of the {len(fb)} new entities; "
                     f"{only['parallel']} came from Parallel alone, {only['exa']} from Exa alone, and {only['firecrawl']} from Firecrawl alone. "
                     f"{sum(1 for f in fb if len(f) > 1)} were found by more than one provider.")

    # ---- S6 (verdicts after Amendment 7 come from scored_b.json) ----
    s6 = sc["S6"]
    vals = s6["values"]
    gt = s6["gt_cells"]
    nogt = 160 - gt
    ans_gt = {p: sum(s6[p].get(k, 0) for k in ("gt_correct", "gt_correction", "gt_partly_correct", "gt_wrong")) for p in P}
    right = {p: s6[p].get("gt_correct", 0) + s6[p].get("gt_correction", 0) for p in P}
    right_j = {p: int(s6[p]["gt_right_of_gt_cells_as_judged"].split("/")[0]) for p in P}
    cv_nw = {p: (s6[p].get("cite_yes_not_wrong", 0), s6[p].get("cite_no_not_wrong", 0)) for p in P}
    T["s6_main"] = "\n".join([HDR,
        row("Cells filled (of 160)", {p: pct(s6[p]["non_null"], 160) for p in P}),
        row(f"Ground-truth cells answered (of {gt})", ans_gt),
        row("… match the KB", {p: s6[p].get("gt_correct", 0) for p in P}),
        row("… verified corrections to the KB", {p: s6[p].get("gt_correction", 0) for p in P}),
        row("… partly correct", {p: s6[p].get("gt_partly_correct", 0) for p in P}),
        row("… wrong", {p: s6[p].get("gt_wrong", 0) for p in P}),
        row(f"Right, strict: match or correction (of {gt})", {p: pct(right[p], gt) + (f"; {right_j[p]} as judged" if right_j[p] != right[p] else "") for p in P}),
        row("Right, strict, of the ground-truth cells it answered", {p: pct(right[p], ans_gt[p]) for p in P}),
        row(f"Right, broad: adds partly correct (of {gt})", {p: pct(right[p] + s6[p].get("gt_partly_correct", 0), gt) for p in P}),
        row(f"Cells without ground truth answered (of {nogt})", {p: sum(s6[p].get(k, 0) for k in ("nogt_verified", "nogt_partly_verified", "nogt_wrong", "nogt_unverifiable")) for p in P}),
        row("… verified / partly / wrong / unverifiable", {p: f"{s6[p].get('nogt_verified', 0)} / {s6[p].get('nogt_partly_verified', 0)} / {s6[p].get('nogt_wrong', 0)} / {s6[p].get('nogt_unverifiable', 0)}" for p in P}),
        row("False-claim rate: wrong ÷ values given", {p: pct(s6[p].get("wrong", 0), s6[p]["non_null"]) for p in P}),
        row("… leaving out values that don't answer the field", {p: pct(s6[p].get("wrong", 0) - s6[p].get("wrong_non_responsive", 0), s6[p]["non_null"] - s6[p].get("wrong_non_responsive", 0)) for p in P}),
        row("Cited page supports the value, values not judged wrong (of those checked)", {p: pct(cv_nw[p][0], sum(cv_nw[p])) for p in P}),
        row("Values judged wrong whose cited page the judge marked valid", {p: s6[p].get("wrong_with_valid_citation", 0) for p in P}),
        row(f"Cells where it added a verified new fact ({s6['novel_cells_kb_new']} cells had one)", {p: s6[p].get("novel_kb_new", 0) for p in P}),
        row("… and no other provider did", {p: s6[p].get("novel_kb_new_unique", 0) for p in P}),
        row("Substantive corrections (of the correction values)", {p: f"{s6[p].get('correction_substantive', 0)} of {s6[p].get('gt_correction', 0)}" for p in P})])
    bt = s6["correction_cells_by_type"]
    T["s6_any"] = (f"{s6['novel_cells_kb_new']} of the 160 cells gained a verified new fact from at least one provider: {s6['correction_cells']} corrections "
                   f"to a KB value and {s6['novel_cells_kb_new'] - s6['correction_cells']} cells the KB left empty. Of the corrections, "
                   + ", ".join(f"{bt.get(k, 0)} {v}" for k, v in CTYPE.items()) +
                   f" (Amendment 7). Another {s6['novel_cells_any_provider'] - s6['novel_cells_kb_new']} cells had verified values for facts the KB "
                   f"already states; those add a citation at most.")
    # tests: value-level Fisher tests and cell- and company-level sign tests. Values cluster by company (one Exa or
    # Parallel run per company, one Firecrawl run per five), so the company-level tests are the conservative ones.
    def fisher_pair(x, y, a_x, n_x, a_y, n_y):
        return fisher_p(a_x, n_x - a_x, a_y, n_y - a_y)
    tests = []
    for x, y in (("parallel", "exa"), ("exa", "firecrawl"), ("parallel", "firecrawl")):
        tests.append(f"{NAME[x]} vs {NAME[y]}: completeness p {fmt_p(fisher_pair(x, y, s6[x]['non_null'], 160, s6[y]['non_null'], 160))}, "
                     f"false-claim rate p {fmt_p(fisher_pair(x, y, s6[x].get('wrong', 0), s6[x]['non_null'], s6[y].get('wrong', 0), s6[y]['non_null']))}, "
                     f"citation validity (not wrong) p {fmt_p(fisher_pair(x, y, cv_nw[x][0], sum(cv_nw[x]), cv_nw[y][0], sum(cv_nw[y])))}")
    T["s6_fisher"] = "Fisher exact tests on values (two-sided, uncorrected): " + "; ".join(tests) + "."
    right_gt = {p: {v["cell"] for v in vals if v["provider"] == p and v["has_gt"] and v["verdict"] in ("correct", "correction")} for p in P}
    comp = sorted({v["company_id"] for v in vals})
    by_co = {p: {c: sum(1 for cell in right_gt[p] if cell.startswith(c + ".")) for c in comp} for p in P}
    pairs = []
    for x, y in (("parallel", "exa"), ("exa", "firecrawl"), ("parallel", "firecrawl")):
        a, b = len(right_gt[x] - right_gt[y]), len(right_gt[y] - right_gt[x])
        ca = sum(1 for c in comp if by_co[x][c] > by_co[y][c]); cb = sum(1 for c in comp if by_co[x][c] < by_co[y][c])
        pairs.append(f"{NAME[x]} vs {NAME[y]}: by cell {a}–{b} (p {fmt_p(sign_p(a, b))}); by company {ca}–{cb} with {len(comp) - ca - cb} ties (p {fmt_p(sign_p(ca, cb))})")
    T["s6_paired"] = ("Paired sign tests on the 133 ground-truth cells (strict; two-sided, uncorrected for the three comparisons): "
                      + "; ".join(pairs) + ". The by-company tests respect how values cluster within one run.")
    J["S6_tests"] = {"fisher": tests, "paired": pairs}
    # empty cells, by the answer key's kind
    pool6 = {i["cell"]: i for i in json.loads((B / "S6_pool.json").read_text())}
    attrib = json.loads((B / "_attrib" / "S6_attribution.json").read_text())
    empt = []
    for p in P:
        kinds = collections.Counter()
        for cell, item in pool6.items():
            if not any(attrib[v["vid"]] == p for v in item["values"]):
                kinds[(item["kb_ground_truth"] or {}).get("kind", "no ground truth")] += 1
        empt.append(f"{NAME[p]} {sum(kinds.values())} (" + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())) + ")")
    T["s6_empty"] = "Cells left empty, by the answer key's kind: " + "; ".join(empt) + "."
    # per field
    per = collections.defaultdict(collections.Counter)
    for v in vals:
        per[(v["field"], v["provider"])]["given"] += 1
        per[(v["field"], v["provider"])][v["verdict"]] += 1
    lines = ["| Field | Ground-truth cells | " + " | ".join(NAME[p] for p in P) + " |", "|---|---|" + "---|" * len(P)]
    for f in FIELD:
        n_gt = sum(1 for i in pool6.values() if i["field"] == f and i["kb_ground_truth"])
        cellf = lambda p: (lambda c: f"{c['correct'] + c['correction'] + c['verified']}/{c['given']}" + (f", {c['wrong']} wrong" if c["wrong"] else ""))(per[(f, p)])
        lines.append(f"| {FIELD[f]} | {n_gt} of 20 | " + " | ".join(cellf(p) for p in P) + " |")
    T["s6_fields"] = "Each cell: values judged right (match, correction, or verified) ÷ values given, and the wrong count.\n\n" + "\n".join(lines)
    # cost per right value
    rv = {p: right[p] + s6[p].get("nogt_verified", 0) for p in P}
    fc_rows_paid = 5 * fc_paid["S6"]
    T["s6_cost"] = "\n".join([HDR,
        row("Values judged right (match, correction, or verified)", rv),
        row("S6 spend", {"exa": f"${usd(r6, 'exa'):.2f}", "parallel": f"${usd(r6, 'parallel'):.2f}", "firecrawl": f"{fc_cred['S6']} credits for {fc_rows_paid} of 20 companies"}),
        row("Spend per company", {"exa": f"${usd(r6, 'exa') / 20:.2f}", "parallel": f"${usd(r6, 'parallel') / 20:.2f}",
            "firecrawl": f"{fc_cred['S6'] / fc_rows_paid:.0f} credits ({fc_money(fc_cred['S6'] / fc_rows_paid)} by plan) on the paid runs" if fc_rows_paid else "—"}),
        row("Spend per right value", {"exa": f"${usd(r6, 'exa') / rv['exa']:.4f}", "parallel": f"${usd(r6, 'parallel') / rv['parallel']:.4f}",
            "firecrawl": "not measurable (half the rows ran free)"})])
    # findings: corrections (with their type) and new facts, one row per cell
    judged = {}
    for f in sorted(B.glob("S6_c*_judge.json")):
        for c in json.loads(f.read_text())["cells"]:
            for v in c["values"]:
                judged[v["vid"]] = v
    final = {v["vid"]: v["verdict"] for v in vals}
    chk = json.loads((B / "S6_nogt_kb_check.json").read_text())["cells"] if (B / "S6_nogt_kb_check.json").exists() else {}
    ctypes = json.loads((B / "S6_correction_types.json").read_text())["cells"] if (B / "S6_correction_types.json").exists() else {}
    corr = ["| Company | Field | Type | What the verified value changes (judge's paraphrase) | Verified on | Credited |", "|---|---|---|---|---|---|"]
    new = ["| Company | Field | Verified fact (judge's paraphrase) | Type | Verified on | Credited |", "|---|---|---|---|---|---|"]
    for cell, item in sorted(pool6.items()):
        good = [(attrib[v["vid"]], judged[v["vid"]]) for v in item["values"] if final[v["vid"]] in ("correction", "verified")]
        if not good or (not item["kb_ground_truth"] and chk.get(cell, {}).get("kb_states_it")):
            continue
        if item["kb_ground_truth"]:
            good = [g for g in good if final[g[1]["vid"]] == "correction"]
            if not good:
                continue
        clean = [g for g in good if not re.search(r"\bv\d\b", g[1].get("note") or "")] or good
        best = max(clean, key=lambda g: len(g[1].get("note") or ""))
        who = ", ".join(NAME[p] for p in P if p in {g[0] for g in good})
        if item["kb_ground_truth"]:
            corr.append(f"| {item['company']} | {FIELD[item['field']]} | {CTYPE.get(ctypes.get(cell, {}).get('type'), '—')} | {trim(best[1].get('note'))} | {src(best[1].get('checked_url'))} | {who} |")
        else:
            new.append(f"| {item['company']} | {FIELD[item['field']]} | {trim(best[1].get('note'), 200)} | {chk.get(cell, {}).get('fact_type', '—')} | {src(best[1].get('checked_url'))} | {who} |")
    T["findings_s6_corrections"] = "\n".join(corr)
    T["findings_s6_new"] = "\n".join(new)
    multi = sum(1 for r in corr[2:] if len(r.rstrip(" |").rsplit("|", 1)[1].split(",")) > 1)
    single = [r for r in corr[2:] if len(r.rstrip(" |").rsplit("|", 1)[1].split(",")) == 1]
    soft = sum(1 for r in single if "| substantive |" not in r)
    T["s6_corr_overlap"] = (f"{multi} of the {len(corr) - 2} correction cells were found by two or three providers, and {len(single)} by one. "
                            f"{soft} of those {len(single)} single-provider corrections aren't substantive.")
    J["S6"] = {k: v for k, v in s6.items() if k != "values"}

    # ---- SP (Exa only, not blind) ----
    spd = json.loads((DATA / "grading" / "SP_domains.json").read_text()) if (DATA / "grading" / "SP_domains.json").exists() else None
    spj = json.loads((B / "SP_judge.json").read_text())["domains"]
    cls = collections.Counter(d["class"] for d in spj)
    n_res = sum(len(r.get("results") or []) for r in rp.values())
    doms = {reg(x["url"]) for r in rp.values() for x in (r.get("results") or []) if x.get("url")}
    vend = [d for d in spj if d["class"] == "relevant_vendor"]
    T["sp"] = "\n".join(["| Exa find-similar, 10 competitor homepages | Count |", "|---|---|",
        f"| Results returned | {n_res} |", f"| Distinct registrable domains | {len(doms)} |",
        f"| … already cited in the KB | {len(doms) - (len(spd['novel']) if spd else 0)} |", f"| … new to the KB | {len(spd['novel']) if spd else '—'} |",
        f"| New domains: relevant vendors (search, SERP, crawling, or web-data APIs) | {cls['relevant_vendor']} |",
        f"| New domains: profiles, directories, or reviews of a seed company | {cls['profile_or_directory']} |",
        f"| New domains: clones or unrelated | {cls['clone_or_unrelated']} |",
        f"| Relevant vendors the KB baseline already covers | {sum(1 for d in vend if d.get('in_kb'))} |",
        f"| Spend | ${usd(rp, 'exa'):.2f} |"])
    T["findings_sp"] = "\n".join(["| Domain | What it sells (judge's summary) | Judge's note |", "|---|---|---|"]
                                 + [f"| {d['domain']} | {trim(d.get('sells'), 120)} | {trim(d.get('note'), 160)} |" for d in vend])
    J["SP"] = {"results": n_res, "domains": len(doms), "classes": dict(cls), "relevant_vendors": [d["domain"] for d in vend]}

    # ---- SM ----
    sm = recs("SM")
    created = sum(1 for r in sm if r.get("action") == "create" and r.get("status") in (200, 201))
    refused = sum(1 for r in sm if r.get("action") == "create" and r.get("status") == 400)
    T["sm"] = (f"{created} monitors were created on 2026-09-26 after {refused} first attempts were refused (HTTP 400: Exa rejects example.com as a webhook). "
               "They run daily until 2026-10-10. Results are polled through the API, graded, and written up then; the monitors are deleted on that date.")

    # ---- glance (README) ----
    T["glance_b"] = "\n".join([HDR,
        row("S5 entities verified and new to the KB (3 lists, fact-level rule)", {p: tot[p]["verified_novel"] for p in P}),
        row("… no other provider returned it", {p: tot[p]["verified_novel_unique"] for p in P}),
        row("S5 precision: entities meeting every criterion", {p: pct(tot[p]["meets"], tot[p]["returned"]) for p in P}),
        row(f"S6 right on KB ground truth, strict (of {gt} cells)", {p: pct(right[p], gt) for p in P}),
        row("S6 cells filled (of 160)", {p: pct(s6[p]["non_null"], 160) for p in P}),
        row("S6 false-claim rate", {p: pct(s6[p].get("wrong", 0), s6[p]["non_null"]) for p in P}),
        row(f"S6 cells where it added a verified new fact (of {s6['novel_cells_kb_new']})", {p: s6[p].get("novel_kb_new", 0) for p in P}),
        row("Stage B spend", {"exa": f"${led['exa']['usd']:.2f}", "parallel": f"${led['parallel']['usd']:.2f} (list price)",
            "firecrawl": f"{led['firecrawl']['credits']} credits ({fc_money(led['firecrawl']['credits'])}), plus 5 free runs"})])

    (B / "tables_stage_b.json").write_text(json.dumps(J, indent=1, default=lambda o: dict(o)))
    for md in args:
        p = pathlib.Path(md)
        s = p.read_text()
        for k, v in T.items():
            s = re.sub(rf"(<!-- tables:{k}:start -->)\n?.*?\n?(<!-- tables:{k}:end -->)", lambda m: m.group(1) + "\n" + v + "\n" + m.group(2), s, flags=re.S)
        p.write_text(s)
    for k, v in T.items():
        print(f"== {k}\n{v}\n")


if __name__ == "__main__":
    main()
