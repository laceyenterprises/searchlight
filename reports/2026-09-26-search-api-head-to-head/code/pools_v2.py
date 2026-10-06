#!/usr/bin/env python3
"""Stage A re-grading pools (v2), per Amendment 4.

Differences from grade_prep.py's pools (v1):
1. Each pool's result order is shuffled with a recorded seed ("20260926-<set>-<item id>"), and rids are renumbered
   after the shuffle, so slot position no longer tracks provider order (v1 always put Exa's results first).
2. The KB baseline is one checkout at a recorded commit, with experiments/ excluded, so the experiment's own data
   can't mark URLs as already cited.

Usage: python3 pools_v2.py <kb checkout at the baseline commit>
Writes grading/v2/{S1,S4}_pools_blind.json, grading/v2/_attrib/{S1,S4}_attribution.json, grading/v2/pool_meta.json.
"""
import json, os, pathlib, random, re, subprocess, sys

import grade_prep as G  # norm(), load(), PROVIDERS: imported, not modified

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "grading" / "v2"
(OUT / "_attrib").mkdir(parents=True, exist_ok=True)


def kb_urls(checkout):
    pat = re.compile(r"https?://[^\s)\]>\"'`|]+")
    urls = set()
    for root, dirs, files in os.walk(checkout):
        dirs[:] = [d for d in dirs if d not in (".git", "experiments")]
        for fn in files:
            if fn.endswith((".md", ".json", ".txt")):
                text = open(os.path.join(root, fn), encoding="utf-8", errors="ignore").read()
                urls.update(G.norm(u) for u in pat.findall(text))
    return urls


def main():
    co = sys.argv[1]
    commit = subprocess.run(["git", "-C", co, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    KB = kb_urls(co)
    sets = {s: json.loads((ROOT / "sets" / f"{s}.json").read_text()) for s in ["S1", "S4"]}
    meta = {"baseline_commit": commit, "kb_urls": len(KB), "seed_rule": "random.Random('20260926-<set>-<item id>').shuffle",
            "excluded_dirs": [".git", "experiments"]}
    for set_name in ["S1", "S4"]:
        recs = G.load(set_name)
        pools, attrib = [], {}
        for it in sets[set_name]["items"]:
            pooled, order = {}, []
            for p in G.PROVIDERS:
                r = recs.get((p, it["id"]))
                if not r or r["status"] != 200:
                    continue
                for x in r["results"][:10]:
                    k = G.norm(x["url"])
                    if not k:
                        continue
                    if k not in pooled:
                        pooled[k] = {"url": x["url"], "title": x.get("title"), "published": x.get("published"),
                                     "text": (x.get("text") or "")[:700], "in_kb": k in KB}
                        order.append(k)
                    elif len(x.get("text") or "") > len(pooled[k]["text"]):
                        pooled[k]["text"] = (x.get("text") or "")[:700]
                    attrib.setdefault(f"{it['id']}|{k}", set()).add(p)
            random.Random(f"20260926-{set_name}-{it['id']}").shuffle(order)
            items = [{"rid": f"{it['id']}-r{n}", "key": k, **pooled[k]} for n, k in enumerate(order, 1)]
            pools.append({"id": it["id"], "question": it["question"], "kb_context_at_stage_a": it.get("kb_context", ""), "results": items})
        (OUT / f"{set_name}_pools_blind.json").write_text(json.dumps(pools, indent=1, ensure_ascii=False))
        (OUT / "_attrib" / f"{set_name}_attribution.json").write_text(json.dumps({k: sorted(v) for k, v in attrib.items()}, indent=1))
        meta[f"{set_name}_pooled_results"] = sum(len(p["results"]) for p in pools)
        meta[f"{set_name}_in_kb"] = sum(r["in_kb"] for p in pools for r in p["results"])
    (OUT / "pool_meta.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
