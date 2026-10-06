#!/usr/bin/env python3
"""Generate Stage A's hard-number tables from the run records and scored files, so the write-up can't drift.

Reads runs/ (records, ledger, diagnostics) and grading/ (v1 and v2 scores). Writes grading/tables_stage_a.json, and
replaces the block between <!-- tables:<name>:start --> and <!-- tables:<name>:end --> markers in any markdown file
passed on the command line.

Usage: python3 make_tables_a.py [results-stage-a.md README.md ...]
"""
import collections, json, pathlib, re, statistics, sys
from urllib.parse import urlsplit

import grade_prep as G

ROOT = pathlib.Path(__file__).resolve().parent
P = ["exa", "tavily", "parallel", "firecrawl"]
NAME = {"exa": "Exa", "tavily": "Tavily", "parallel": "Parallel", "firecrawl": "Firecrawl"}
TAV, FC_LO, FC_HI = 0.008, 0.00075, 0.005   # $ per credit: Tavily PAYG; Firecrawl Scale monthly to Hobby top-up


def recs(set_name):
    out = {}
    for p in P:
        f = ROOT / "runs" / set_name / f"{p}.jsonl"
        if f.exists():
            for line in open(f):
                r = json.loads(line)
                out[(p, r["id"])] = r  # last record wins, as in grading
    return out


def row(label, vals, fmt=str):
    return "| " + label + " | " + " | ".join(fmt(vals[p]) for p in P) + " |"


HDR = "| | " + " | ".join(NAME[p] for p in P) + " |\n|---|" + "---|" * len(P)


def reg_domain(u):
    h = (urlsplit(u if "://" in u else "https://" + u).hostname or "").lower()
    h = h[4:] if h.startswith("www.") else h
    parts = h.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else h


def s2_scores(records, items):
    strict, dom, n = collections.Counter(), collections.Counter(), collections.Counter()
    for it in items:
        exp = [G.norm(e) for e in it["expected_urls"]]
        for p in P:
            r = records.get((p, it["id"]))
            if not r or r["status"] != 200:
                continue
            n[p] += 1
            got = [G.norm(x["url"]) for x in r["results"][:10]]
            if any(g.startswith(e) or e.startswith(g) and len(g) > len(e) * 0.8 for g in got for e in exp):
                strict[p] += 1
            if {reg_domain(e) for e in exp} & {reg_domain(g) for g in got}:
                dom[p] += 1
    return strict, dom, n


def main():
    T, J = {}, {}
    # ---- spend and calls (Stage A runs plus post-hoc diagnostics; the ledger counts everything) ----
    led = json.loads((ROOT / "runs" / "ledger.json").read_text())
    calls = {p: sum(1 for s in ["S1", "S2", "S3", "S4"] for (q, _) in recs(s) if q == p) for p in P}
    usd = {"exa": led["exa"]["usd"], "parallel": led["parallel"]["usd"], "tavily": led["tavily"]["credits"] * TAV,
           "firecrawl": (led["firecrawl"]["credits"] * FC_LO, led["firecrawl"]["credits"] * FC_HI)}
    spend_txt = {"exa": f"${led['exa']['usd']:.2f}", "parallel": f"${led['parallel']['usd']:.2f} (list price)",
                 "tavily": f"{led['tavily']['credits']} credits (${usd['tavily']:.2f} at $0.008)",
                 "firecrawl": f"{led['firecrawl']['credits']} credits (${usd['firecrawl'][0]:.2f}–${usd['firecrawl'][1]:.2f} by plan)"}
    diag = {p: {"usd": 0.0, "credits": 0} for p in P}
    for f in (ROOT / "runs" / "diag").glob("*.jsonl"):
        for line in open(f):
            r = json.loads(line)
            p = r.get("provider") or ("exa" if r.get("diag") == "D1" else "firecrawl" if r.get("diag") == "D3" else None)
            c = r.get("cost") or {}
            if p:
                diag[p]["usd"] += c.get("usd") or 0.0
                diag[p]["credits"] += c.get("credits") or 0
    base = {p: {"usd": led[p]["usd"] - diag[p]["usd"], "credits": led[p]["credits"] - diag[p]["credits"]} for p in P}
    fmt_sp = lambda d, p: (f"${d[p]['usd']:.2f}" if p in ("exa", "parallel") else
                           f"{d[p]['credits']} credits (${d[p]['credits'] * TAV:.2f})" if p == "tavily" else
                           f"{d[p]['credits']} credits (${d[p]['credits'] * FC_LO:.2f}–${d[p]['credits'] * FC_HI:.2f})")
    T["cost"] = "\n".join([HDR, row("Question-level records, S1–S4", calls), row("API calls billed, all Stage A work (incl. scrapes and diagnostics)", {p: led[p]["calls"] for p in P}),
                           row("Spend, Stage A runs", {p: fmt_sp(base, p) for p in P}),
                           row("Spend, post-hoc diagnostics D1–D3", {p: fmt_sp(diag, p) for p in P})])
    J["cost_split"] = {"stage_a_runs": base, "diagnostics": diag}
    J["cost"] = {"calls_records": calls, "ledger": led}
    # ---- latency ----
    lat = {p: sorted(r["latency_ms"] for s in ["S1", "S2", "S3", "S4"] for (q, _), r in recs(s).items() if q == p and r["status"] == 200) for p in P}
    med = {p: int(statistics.median(v)) for p, v in lat.items()}
    p90 = {p: v[int(0.9 * (len(v) - 1))] for p, v in lat.items()}
    T["latency"] = "\n".join([HDR, row("Median latency, ms (successful calls, S1–S4)", med, lambda x: f"{x:,}"),
                              row("90th percentile, ms", p90, lambda x: f"{x:,}"), row("Calls measured", {p: len(v) for p, v in lat.items()})])
    J["latency"] = {"median_ms": med, "p90_ms": p90}
    # ---- S2 ----
    items = json.loads((ROOT / "sets" / "S2.json").read_text())["items"]
    s_str, s_dom, s_n = s2_scores(recs("S2"), items)
    d2 = {}
    for p in P:
        f = ROOT / "runs" / "diag" / f"D2_S2_identical_{p}.jsonl"
        if f.exists():
            for line in open(f):
                r = json.loads(line)
                d2[(p, r["id"])] = r
    d_str, d_dom, d_n = s2_scores(d2, items)
    frac = lambda a, b: (lambda p: f"{a[p]}/{b[p]}")
    T["s2"] = "\n".join([HDR, row("Strict, pre-registered inputs (exact cited URL, prefix match)", {p: f"{s_str[p]}/{s_n[p]}" for p in P}),
                         row("Domain-level, pre-registered inputs (post hoc)", {p: f"{s_dom[p]}/{s_n[p]}" for p in P}),
                         row("Strict, identical inputs (D2, post hoc)", {p: f"{d_str[p]}/{d_n[p]}" for p in P}),
                         row("Domain-level, identical inputs (D2)", {p: f"{d_dom[p]}/{d_n[p]}" for p in P})])
    J["s2"] = {"strict": s_str, "domain": s_dom, "n": s_n, "d2_strict": d_str, "d2_domain": d_dom, "d2_n": d_n}
    # ---- S3 ----
    s3items = {i["id"]: i for i in json.loads((ROOT / "sets" / "S3.json").read_text())["items"]}
    r3 = recs("S3")
    def rec_ok(r, it):
        c = r.get("content") or ""
        return r["status"] == 200 and r.get("content_chars", len(c)) >= 500 and any(ph.lower() in c.lower() for ph in it["target_phrases"])
    raw = {p: sum(rec_ok(r3[(p, i)], s3items[i]) for i in s3items if (p, i) in r3) for p in P}
    corr = {p: raw[p] - int(rec_ok(r3[(p, "b04")], s3items["b04"])) if (p, "b04") in r3 else raw[p] for p in P}
    exa_err = [i for i in s3items if (("exa", i) in r3) and any((d or {}).get("status") == "error" for d in (r3[("exa", i)].get("detail") or []))]
    exa_miss = [i for i in s3items if ("exa", i) in r3 and not rec_ok(r3[("exa", i)], s3items[i])]
    d1 = [json.loads(l) for l in open(ROOT / "runs" / "diag" / "D1_exa_contents_default.jsonl")] if (ROOT / "runs" / "diag" / "D1_exa_contents_default.jsonl").exists() else []
    d1_rec = sorted(r["id"] for r in d1 if r["recovered"])
    T["s3"] = "\n".join([HDR, row("Scripted: ≥500 characters and a target keyword", {p: f"{raw[p]}/15" for p in P}),
                         row("After the b04 correction (CodeRabbit redirect), reported", {p: f"{corr[p]}/15" for p in P})])
    T["s3_exa"] = (f"Exa's {15 - corr['exa']} misses (after the b04 correction): {len(exa_err)} were crawl errors in its own status field under the forced live crawl "
                   f"(`maxAgeHours: 0`): {', '.join(exa_err)}. With default cache settings (D1, post hoc), {len(d1_rec)} of those "
                   f"{len(d1)} came back readable ({', '.join(d1_rec)}), which would put Exa at {corr['exa'] + len(d1_rec)}/15.")
    J["s3"] = {"scripted": raw, "corrected": corr, "exa_crawl_errors": exa_err, "exa_misses": exa_miss, "d1_recovered": d1_rec}
    low = []
    for (p_, i), r in sorted(r3.items()):
        if i != "b04" and rec_ok(r, s3items[i]) and r.get("content_chars", 0) < 2000:
            c = (r.get("content") or "").lower()
            low.append(f"{NAME[p_]} {i} ({r.get('content_chars', 0):,} chars; matched {', '.join(ph for ph in s3items[i]['target_phrases'] if ph.lower() in c)})")
    T["s3_low"] = (f"{len(low)} of the counted recoveries rest on fewer than 2,000 characters: " + "; ".join(low) + "."
                   if low else "No counted recovery rests on fewer than 2,000 characters.")
    # ---- S1 / S4: v2 (primary) and v1 (superseded) ----
    v2f = ROOT / "grading" / "v2" / "scored_v2.json"
    if v2f.exists():
        v2 = json.loads(v2f.read_text())
        for mode in ("strict", "broad"):
            s1 = v2["S1"][mode]
            T[f"s1_{mode}"] = "\n".join([HDR,
                row("Questions with a verified new or correcting claim (of 40)", {p: s1[p].get("questions_new", 0) for p in P}),
                row("… of them in q01–q20", {p: s1[p].get("questions_new_q01_q20", 0) for p in P}),
                row("New or correcting claims credited", {p: s1[p].get("new_claims", 0) for p in P}),
                row("… of which correct a KB claim", {p: s1[p].get("corrections", 0) for p in P}),
                row("Claims no other provider supported", {p: s1[p].get("unique_new_claims", 0) for p in P}),
                row("Questions the provider's own results fully resolve", {p: s1[p].get("resolved_alone", 0) for p in P}),
                row("Usefulness-weighted score", {p: s1[p].get("weighted", 0) for p in P})])
            s4 = v2["S4"][mode]
            T[f"s4_{mode}"] = "\n".join([HDR,
                row("Verified new facts (usefulness ≥ 2)", {p: s4[p].get("new_facts", 0) for p in P}),
                row("… net-new events", {p: s4[p].get("new_facts_net_new", 0) for p in P}),
                row("… new details on known events", {p: s4[p].get("new_facts_new_detail", 0) for p in P}),
                row("Facts no other provider supported", {p: s4[p].get("unique_new_facts", 0) for p in P}),
                row("Events with at least one new fact", {p: s4[p].get("new_events", 0) for p in P}),
                row("Events unique to the provider", {p: s4[p].get("unique_new_events", 0) for p in P})])
        T["s1_any"] = (f"{len(v2['S1']['questions_with_new_answer']['strict'])} of 40 questions got a verified new or correcting claim "
                       f"that some provider's own result page states (strict); {len(v2['S1']['questions_with_new_answer']['broad'])} counting partial support.")
        sr = v2["S1"]["stop_rule_q01_q20"]
        T["stop_rule"] = (f"Re-checked under v2 for q01–q20: the most questions any one provider fully resolved alone was "
                          f"{sr['strict']['max_resolved_alone']} (strict) and {sr['broad']['max_resolved_alone']} (broad); the most with a new "
                          f"claim was {sr['strict']['max_questions_new']} (strict) and {sr['broad']['max_questions_new']} (broad).")
        n_new = {p: v2["S1"]["strict"][p].get("new_claims", 0) + v2["S4"]["strict"][p].get("new_facts", 0) for p in P}
        def per(p):
            if not n_new[p]:
                return "—"
            if p in ("exa", "parallel"):
                return f"${base[p]['usd'] / n_new[p]:.3f}"
            if p == "tavily":
                return f"${base[p]['credits'] * TAV / n_new[p]:.3f}"
            return f"${base[p]['credits'] * FC_LO / n_new[p]:.3f}–${base[p]['credits'] * FC_HI / n_new[p]:.3f}"
        T["cost_per_item"] = "\n".join([HDR, row("New S1 claims plus S4 facts, strict credit", n_new),
                                         row("Stage A run spend per new item", {p: per(p) for p in P})])
        ov = v2["overlap"]
        T["overlap"] = ("New S1 claims by number of providers supporting them (strict): "
                        + ", ".join(f"{k}: {v}" for k, v in ov["S1_new_claims"]["by_number_of_providers"].items())
                        + ". S4 new facts: " + ", ".join(f"{k}: {v}" for k, v in ov["S4_new_facts"]["by_number_of_providers"].items()) + ".")
        J["v2"] = {k: v for k, v in v2.items() if k != "rows"}
        # findings tables: every new claim or fact, with its verified source and strict credit
        def src(u):
            if not u:
                return "—"
            d = reg_domain(u)
            if d in ("x.com", "twitter.com", "linkedin.com", "instagram.com", "facebook.com"):
                return f"{d} post (URL in the local grading data)"
            return f"[{d}]({u})"
        jq = {}
        for f in sorted((ROOT / "grading" / "v2").glob("S1_c*_judge.json")):
            for q in json.loads(f.read_text())["questions"]:
                jq[q["id"]] = q
        rows1 = ["| Q | Claim (judge's paraphrase) | Novelty | Use | Verified on | Credited (strict) |", "|---|---|---|---|---|---|"]
        for r in v2["rows"]["S1_claims"]:
            if r["novelty"] in ("net_new", "contradicts_kb") and r["usefulness"] >= 2:
                c = jq[r["q"]]["claims"][r["claim"]]
                txt = c["claim"].replace("|", "/")
                txt = txt if len(txt) <= 220 else txt[:217].rsplit(" ", 1)[0] + "…"
                rows1.append(f"| {r['q']} | {txt} | {'correction' if r['novelty'] == 'contradicts_kb' else 'new'} | {r['usefulness']} | {src(c.get('verified_url'))} | {', '.join(NAME[p] for p in r['strict']) or 'none (partial support only)'} |")
        T["findings_s1"] = "\n".join(rows1)
        jf = {}
        for f in sorted((ROOT / "grading" / "v2").glob("S4_c*_judge.json")):
            for co in json.loads(f.read_text())["companies"]:
                jf[co["id"]] = co
        rows4 = ["| Co. | Fact (judge's paraphrase) | Novelty | Source type | Verified on | Credited (strict) |", "|---|---|---|---|---|---|"]
        for r in v2["rows"]["S4_facts"]:
            if r["novelty"] in ("net_new", "new_detail") and r["usefulness"] >= 2:
                fct = jf[r["co"]]["events"][r["event"]]["facts"][r["fact"]]
                txt = fct["fact"].replace("|", "/")
                txt = txt if len(txt) <= 200 else txt[:197].rsplit(" ", 1)[0] + "…"
                rows4.append(f"| {r['co']} | {txt} | {r['novelty'].replace('_', ' ')} | {r['source_type']} | {src(fct.get('verified_url'))} | {', '.join(NAME[p] for p in r['strict']) or 'none (partial support only)'} |")
        T["findings_s4"] = "\n".join(rows4)
        v1q = {r["id"] for r in json.loads((ROOT / "grading" / "S1_scored.json").read_text())["rows"] if r["novelty"] in ("net_new", "contradicts_kb") and r["providers"]}
        v2q = set(v2["S1"]["questions_with_new_answer"]["strict"])
        T["v1_v2_agreement"] = (f"v1 found a new or correcting answer on {len(v1q)} questions; v2 (strict) on {len(v2q)}. They agree on {len(v1q & v2q)}. "
                                f"Only in v1: {', '.join(sorted(v1q - v2q)) or 'none'}. Only in v2: {', '.join(sorted(v2q - v1q)) or 'none'}.")
        s1s, s4s = v2["S1"]["strict"], v2["S4"]["strict"]
        T["glance"] = "\n".join([HDR,
            row("S1 new or correcting claims, strict (known unknowns, 40 questions)", {p: s1s[p].get("new_claims", 0) for p in P}),
            row("… no other provider supported", {p: s1s[p].get("unique_new_claims", 0) for p in P}),
            row("S4 new dated facts, strict (10 companies, two weeks)", {p: s4s[p].get("new_facts", 0) for p in P}),
            row("S3 pages recovered that native tools couldn't read (of 15)", {p: corr[p] for p in P}),
            row("S2 cited sources found in the top 10, identical inputs (of 20)", {p: d_str[p] for p in P}),
            row("Stage A run spend", {p: fmt_sp(base, p) for p in P}),
            row("Median latency, ms", med, lambda x: f"{x:,}")])
    v1 = json.loads((ROOT / "grading" / "S1_scored.json").read_text())["score"]
    T["s1_v1"] = "\n".join([HDR, row("v1: questions with a verified new or correcting answer (per-question credit)", {p: v1[p].get("net_new_or_contra", 0) for p in P}),
                            row("v1: usefulness-weighted", {p: v1[p].get("weighted", 0) for p in P})])
    v1s4 = json.loads((ROOT / "grading" / "S4_facts_scored.json").read_text())
    T["s4_v1"] = "\n".join([HDR, row("v1: new facts (hand-split after unblinding)", v1s4["per_provider"]), row("v1: unique", v1s4["unique"])])
    (ROOT / "grading" / "tables_stage_a.json").write_text(json.dumps(J, indent=1, default=lambda o: dict(o)))
    for md in sys.argv[1:]:
        p = pathlib.Path(md)
        s = p.read_text()
        for k, v in T.items():
            s = re.sub(rf"(<!-- tables:{k}:start -->)\n?.*?\n?(<!-- tables:{k}:end -->)", lambda m: m.group(1) + "\n" + v + "\n" + m.group(2), s, flags=re.S)
        p.write_text(s)
    for k, v in T.items():
        print(f"== {k}\n{v}\n")


if __name__ == "__main__":
    main()
