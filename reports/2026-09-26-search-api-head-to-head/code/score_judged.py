#!/usr/bin/env python3
"""Unblind a judge file: map rids to pooled URL keys, then to providers, and score per provider."""
import json, sys, collections
set_name = sys.argv[1]
judge = json.load(open(f"grading/{set_name}_judge.json"))
pools = json.load(open(f"grading/{set_name}_pools_blind.json"))
attrib = json.load(open(f"grading/{set_name}_attribution.json"))
rid2key = {r["rid"]: (q["id"], r["key"]) for q in pools for r in q["results"]}
P = ["exa", "tavily", "parallel", "firecrawl"]
def provs(rid):
    qid, key = rid2key.get(rid, (None, None))
    return attrib.get(f"{qid}|{key}", [])
score = {p: {"items": 0, "weighted": 0, "unique": 0, "u2_items": 0} for p in P}
rows = []
if set_name == "S4":
    for c in judge["companies"]:
        for it in c["items"]:
            if it.get("verification") != "verified" or it.get("usefulness", 0) < 1:
                continue
            ps = provs(it["rid"])
            rows.append((c["id"], it["rid"], it["usefulness"], it["novelty"], ps, it["event"][:90]))
else:
    for q in judge["questions"]:
        if q.get("verification") != "verified" or q.get("status") == "unresolved":
            continue
        ps = sorted({p for rid in q.get("support_rids", []) for p in provs(rid)})
        rows.append((q["id"], ",".join(q.get("support_rids", [])), q.get("usefulness", 0), q.get("novelty"), ps, (q.get("answer") or "")[:90]))
# dedupe S4 by (company, event) so one event with many copies counts once per provider
seen = set()
for cid, rid, u, nov, ps, ev in rows:
    key = (cid, ev[:60]) if set_name == "S4" else (cid,)
    for p in ps:
        if (key, p) in seen:
            continue
        seen.add((key, p))
        score[p]["items"] += 1
        score[p]["weighted"] += u
        if u >= 2: score[p]["u2_items"] += 1
# unique: events/answers surfaced by exactly one provider
ev_provs = collections.defaultdict(set)
for cid, rid, u, nov, ps, ev in rows:
    key = (cid, ev[:60]) if set_name == "S4" else (cid,)
    ev_provs[(key, u)].update(ps)
for (key, u), ps in ev_provs.items():
    if len(ps) == 1 and u >= 2:
        score[next(iter(ps))]["unique"] += 1
print(json.dumps(score, indent=1))
for r in rows: print(r)
json.dump({"score": score, "rows": [list(map(lambda x: x if not isinstance(x, set) else sorted(x), r)) for r in rows]}, open(f"grading/{set_name}_scored.json", "w"), indent=1)
