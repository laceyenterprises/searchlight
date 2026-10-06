#!/usr/bin/env python3
"""Score the v2 (shuffled, per-claim) Stage A re-grading, per Amendment 4.

Credit rules:
- strict: a provider is credited with a claim (S1) or fact (S4) only if it returned a result whose page the judge
  confirmed states that claim (support_rids_full).
- broad: results that support part of the claim (support_rids_partial) also count. Reported next to strict.
"New" means novelty net_new or contradicts_kb (S1), or net_new or new_detail (S4), with usefulness 2 or more.

Usage: python3 score_v2.py   (reads grading/v2/*_judge.json and grading/v2/_attrib/, writes grading/v2/scored_v2.json)
"""
import collections, itertools, json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent
V = ROOT / "grading" / "v2"
P = ["exa", "tavily", "parallel", "firecrawl"]


def load_pools(s):
    pools = json.loads((V / f"{s}_pools_blind.json").read_text())
    attrib = json.loads((V / "_attrib" / f"{s}_attribution.json").read_text())
    rid2prov = {r["rid"]: set(attrib.get(f"{q['id']}|{r['key']}", [])) for q in pools for r in q["results"]}
    return rid2prov


def provs(rids, rid2prov):
    out = set()
    for r in rids or []:
        out |= rid2prov.get(r, set())
    return out


def judged(s):
    items = []
    for f in sorted(V.glob(f"{s}_c*_judge.json")):
        d = json.loads(f.read_text())
        items += d.get("questions") or d.get("companies") or []
    return items


def s1():
    rid2prov = load_pools("S1")
    qs = judged("S1")
    out = {"strict": {p: collections.Counter() for p in P}, "broad": {p: collections.Counter() for p in P}}
    claims_rows, q_any = [], {"strict": set(), "broad": set()}
    for q in qs:
        if q.get("status") == "unresolved":
            continue
        claims = [c for c in q.get("claims", []) if c.get("verified_url")]
        for mode in ("strict", "broad"):
            credited_new, credited_all = collections.defaultdict(int), collections.defaultdict(set)
            for i, c in enumerate(claims):
                full = provs(c.get("support_rids_full"), rid2prov)
                ps = full if mode == "strict" else full | provs(c.get("support_rids_partial"), rid2prov)
                new = c.get("novelty") in ("net_new", "contradicts_kb") and c.get("usefulness", 0) >= 2
                for p in ps:
                    out[mode][p]["weighted"] += c.get("usefulness", 0)
                    credited_all[p].add(i)
                    if new:
                        out[mode][p]["new_claims"] += 1
                        credited_new[p] += 1
                        if c.get("novelty") == "contradicts_kb":
                            out[mode][p]["corrections"] += 1
                if new and len(ps) == 1:
                    out[mode][next(iter(ps))]["unique_new_claims"] += 1
                if new and ps:
                    q_any[mode].add(q["id"])
                if mode == "strict":
                    claims_rows.append({"q": q["id"], "claim": i, "novelty": c.get("novelty"), "usefulness": c.get("usefulness", 0),
                                        "strict": sorted(full), "broad": sorted(full | provs(c.get("support_rids_partial"), rid2prov))})
            for p in P:
                if credited_new[p]:
                    out[mode][p]["questions_new"] += 1
                    if q["id"] <= "q20":
                        out[mode][p]["questions_new_q01_q20"] += 1
                # "resolved by provider": the question is resolved and this provider's own results support every claim
                if q.get("status") == "resolved" and claims and len(credited_all[p]) == len(claims):
                    out[mode][p]["resolved_alone"] += 1
                    if q["id"] <= "q20":
                        out[mode][p]["resolved_alone_q01_q20"] += 1
    res = {m: {p: dict(out[m][p]) for p in P} for m in out}
    res["questions_with_new_answer"] = {m: sorted(v) for m, v in q_any.items()}
    res["stop_rule_q01_q20"] = {m: {"max_resolved_alone": max(out[m][p]["resolved_alone_q01_q20"] for p in P),
                                    "max_questions_new": max(out[m][p]["questions_new_q01_q20"] for p in P)} for m in out}
    return res, claims_rows


def s4():
    rid2prov = load_pools("S4")
    cos = judged("S4")
    out = {"strict": {p: collections.Counter() for p in P}, "broad": {p: collections.Counter() for p in P}}
    facts_rows, src = [], collections.Counter()
    for c in cos:
        for ei, e in enumerate(c.get("events", [])):
            ev_prov = {"strict": set(), "broad": set()}
            for fi, f in enumerate(e.get("facts", [])):
                if not f.get("verified_url"):
                    continue
                full = provs(f.get("support_rids_full"), rid2prov)
                broad = full | provs(f.get("support_rids_partial"), rid2prov)
                new = f.get("novelty") in ("net_new", "new_detail") and f.get("usefulness", 0) >= 2
                if new:
                    src[f.get("source_type", "unknown")] += 1
                for mode, ps in (("strict", full), ("broad", broad)):
                    for p in ps:
                        out[mode][p]["weighted"] += f.get("usefulness", 0)
                        if new:
                            out[mode][p]["new_facts"] += 1
                            out[mode][p][f"new_facts_{f.get('novelty')}"] += 1
                            ev_prov[mode].add(p)
                    if new and len(ps) == 1:
                        out[mode][next(iter(ps))]["unique_new_facts"] += 1
                facts_rows.append({"co": c["id"], "event": ei, "fact": fi, "novelty": f.get("novelty"), "usefulness": f.get("usefulness", 0),
                                   "source_type": f.get("source_type"), "strict": sorted(full), "broad": sorted(broad)})
            for mode in ev_prov:
                for p in ev_prov[mode]:
                    out[mode][p]["new_events"] += 1
                if len(ev_prov[mode]) == 1:
                    out[mode][next(iter(ev_prov[mode]))]["unique_new_events"] += 1
    res = {m: {p: dict(out[m][p]) for p in P} for m in out}
    res["new_fact_source_types"] = dict(src)
    return res, facts_rows


def overlap(rows, key_new):
    """How many new items each provider shares with each other provider (strict credit)."""
    pair = collections.Counter()
    shares = collections.Counter()
    for r in rows:
        if not key_new(r):
            continue
        ps = r["strict"]
        shares[len(ps)] += 1
        for a, b in itertools.combinations(sorted(ps), 2):
            pair[f"{a}+{b}"] += 1
    return {"by_number_of_providers": dict(sorted(shares.items())), "pairs": dict(pair)}


def main():
    r1, c1 = s1()
    r4, f4 = s4()
    led = json.loads((ROOT / "runs" / "ledger.json").read_text())
    usd = {"exa": led["exa"]["usd"], "parallel": led["parallel"]["usd"],
           "tavily": led["tavily"]["credits"] * 0.008, "firecrawl": (led["firecrawl"]["credits"] * 0.00075, led["firecrawl"]["credits"] * 0.005)}
    cost = {}
    for p in P:
        n = r1["strict"][p].get("new_claims", 0) + r4["strict"][p].get("new_facts", 0)
        u = usd[p]
        cost[p] = {"new_items_strict": n, "spend_usd": u,
                   "usd_per_new_item": (tuple(round(x / n, 3) for x in u) if isinstance(u, tuple) else round(u / n, 3)) if n else None}
    out = {"S1": r1, "S4": r4,
           "overlap": {"S1_new_claims": overlap(c1, lambda r: r["novelty"] in ("net_new", "contradicts_kb") and r["usefulness"] >= 2),
                       "S4_new_facts": overlap(f4, lambda r: r["novelty"] in ("net_new", "new_detail") and r["usefulness"] >= 2)},
           "cost_per_new_item": cost, "rows": {"S1_claims": c1, "S4_facts": f4}}
    (V / "scored_v2.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main()
