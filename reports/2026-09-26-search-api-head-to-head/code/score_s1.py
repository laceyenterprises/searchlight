#!/usr/bin/env python3
"""Score S1 (known unknowns) from blind judge files, unblinded through the pool attribution map.

A question counts for a provider when the judge verified the answer on its source page, the question isn't
unresolved, and the provider returned at least one of the judge's support results.

Usage: python3 score_s1.py grading/S1_judge.json [grading/S1_judge_b.json ...]
"""
import collections, json, sys

P = ["exa", "tavily", "parallel", "firecrawl"]
NEW = {"net_new", "contradicts_kb"}
pools = json.load(open("grading/S1_pools_blind.json"))
attrib = json.load(open("grading/S1_attribution.json"))
rid2key = {r["rid"]: (q["id"], r["key"]) for q in pools for r in q["results"]}


def provs(rid):
    qid, key = rid2key.get(rid, (None, None))
    return set(attrib.get(f"{qid}|{key}", []))


def score(questions):
    s = {p: collections.Counter() for p in P}
    rows = []
    for q in questions:
        if q.get("verification") != "verified" or q.get("status") == "unresolved":
            continue
        ps = sorted(set().union(*[provs(r) for r in q.get("support_rids", [])]) if q.get("support_rids") else set())
        new = q.get("novelty") in NEW
        u = q.get("usefulness", 0)
        rows.append({"id": q["id"], "status": q["status"], "novelty": q.get("novelty"), "usefulness": u, "providers": ps})
        for p in ps:
            s[p][q["status"]] += 1
            s[p]["weighted"] += u
            if new:
                s[p]["net_new_or_contra"] += 1
                if q["status"] == "resolved":
                    s[p]["resolved_new"] += 1
        if new and u >= 2 and len(ps) == 1:
            s[ps[0]]["unique"] += 1
    any_new = sorted(r["id"] for r in rows if r["novelty"] in NEW and r["providers"])
    return {p: dict(s[p]) for p in P}, rows, any_new


if __name__ == "__main__":
    allq = []
    for f in sys.argv[1:]:
        qs = json.load(open(f))["questions"]
        allq += qs
        sc, rows, any_new = score(qs)
        print(f, "questions:", len(qs), "| with a verified new/correcting answer from any provider:", len(any_new), any_new)
        print(json.dumps(sc))
    if len(sys.argv) > 2:
        sc, rows, any_new = score(allq)
        print("ALL", len(allq), "| any provider new/correcting:", len(any_new))
        print(json.dumps(sc))
        json.dump({"score": sc, "rows": rows, "questions_with_new_answer": any_new}, open("grading/S1_scored.json", "w"), indent=1)
