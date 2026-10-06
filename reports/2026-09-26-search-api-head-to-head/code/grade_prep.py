#!/usr/bin/env python3
"""Deterministic grading prep for Stage A.

1. KB URL baseline: every URL cited anywhere in the KB (leaves plus _scratch research notes) on the given checkouts.
2. S2 recall@10: did a provider return the primary source the KB already cites?
3. S3 recovery: did a provider return readable content containing a target phrase?
4. S1/S4 pools: pooled, deduplicated results per question, stripped of provider names, for a blind judge.
   A separate attribution map says which providers returned each pooled URL.

Usage: python3 grade_prep.py <kb_checkout> [<kb_checkout> ...]
Pass checkouts of the KB as it stood before the run (Stage A: the canonical-positioning branch at c54cdf6 and the base
branch). experiments/ is skipped so a merged checkout can't mark the experiment's own URLs as already cited.
"""
import json, os, pathlib, re, sys
from urllib.parse import urlsplit

ROOT = pathlib.Path(__file__).resolve().parent
RUNS = ROOT / "runs"
OUT = ROOT / "grading"
OUT.mkdir(exist_ok=True)
PROVIDERS = ["exa", "tavily", "parallel", "firecrawl"]
KEEP_QUERY = {"news.ycombinator.com", "youtube.com", "hn.algolia.com"}


def norm(u):
    if not u:
        return ""
    u = u.strip().strip("<>").rstrip(".,;)")
    try:
        s = urlsplit(u if "://" in u else "https://" + u)
    except Exception:
        return u.lower()
    host = (s.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+$", "", s.path or "")
    if path.endswith(".md"):
        path = path[:-3]
    q = ("?" + s.query) if (s.query and host in KEEP_QUERY) else ""
    return f"{host}{path}{q}".lower()


def kb_urls(checkouts):
    pat = re.compile(r"https?://[^\s)\]>\"'`|]+")
    urls = set()
    for co in checkouts:
        for root, dirs, files in os.walk(co):
            dirs[:] = [d for d in dirs if d not in (".git", "experiments")]  # the experiment's own data isn't KB baseline
            for fn in files:
                if fn.endswith((".md", ".json", ".txt")):
                    try:
                        text = open(os.path.join(root, fn), encoding="utf-8", errors="ignore").read()
                    except Exception:
                        continue
                    for u in pat.findall(text):
                        urls.add(norm(u))
    return urls


def load(set_name):
    recs = {}
    for p in PROVIDERS:
        f = RUNS / set_name / f"{p}.jsonl"
        if not f.exists():
            continue
        for line in open(f, encoding="utf-8"):
            r = json.loads(line)
            recs[(p, r["id"])] = r  # last record wins (reruns)
    return recs


def main():
    checkouts = sys.argv[1:]
    KB = kb_urls(checkouts)
    (OUT / "kb_urls.txt").write_text("\n".join(sorted(KB)))
    print("KB baseline URLs:", len(KB))
    sets = {s: json.loads((ROOT / "sets" / f"{s}.json").read_text()) for s in ["S1", "S2", "S3", "S4"]}
    summary = {}

    # S2 recall@10
    recs = load("S2")
    s2 = {p: {"hit": 0, "n": 0, "miss": []} for p in PROVIDERS}
    for it in sets["S2"]["items"]:
        exp = [norm(e) for e in it["expected_urls"]]
        for p in PROVIDERS:
            r = recs.get((p, it["id"]))
            if not r or r["status"] != 200:
                continue
            s2[p]["n"] += 1
            got = [norm(x["url"]) for x in r["results"][:10]]
            if any(g.startswith(e) or e.startswith(g) and len(g) > len(e) * 0.8 for g in got for e in exp):
                s2[p]["hit"] += 1
            else:
                s2[p]["miss"].append(it["id"])
    summary["S2_recall_at_10"] = s2

    # S3 recovery
    recs = load("S3")
    s3 = {p: {"recovered": 0, "n": 0, "detail": {}} for p in PROVIDERS}
    for it in sets["S3"]["items"]:
        for p in PROVIDERS:
            r = recs.get((p, it["id"]))
            if not r:
                continue
            s3[p]["n"] += 1
            c = (r.get("content") or "")
            ok = r["status"] == 200 and r.get("content_chars", 0) >= 500 and any(ph.lower() in c.lower() for ph in it["target_phrases"])
            s3[p]["recovered"] += int(ok)
            s3[p]["detail"][it["id"]] = {"status": r["status"], "chars": r.get("content_chars", 0), "recovered": ok}
    summary["S3_recovery"] = s3

    # S1/S4 pools for the blind judge
    for set_name in ["S1", "S4"]:
        recs = load(set_name)
        pools, attrib = [], {}
        for it in sets[set_name]["items"]:
            pooled, order = {}, []
            for p in PROVIDERS:
                r = recs.get((p, it["id"]))
                if not r or r["status"] != 200:
                    continue
                for x in r["results"][:10]:
                    k = norm(x["url"])
                    if not k:
                        continue
                    if k not in pooled:
                        pooled[k] = {"url": x["url"], "title": x.get("title"), "published": x.get("published"), "text": (x.get("text") or "")[:700], "in_kb": k in KB}
                        order.append(k)
                    elif len(x.get("text") or "") > len(pooled[k]["text"]):
                        pooled[k]["text"] = (x.get("text") or "")[:700]
                    attrib.setdefault(f"{it['id']}|{k}", set()).add(p)
            items = []
            for n, k in enumerate(order, 1):
                d = pooled[k]
                items.append({"rid": f"{it['id']}-r{n}", "key": k, **d})
            pools.append({"id": it["id"], "question": it["question"], "kb_context": it.get("kb_context", ""), "results": items})
        # blind file: no provider names
        (OUT / f"{set_name}_pools_blind.json").write_text(json.dumps(pools, indent=1, ensure_ascii=False))
        (OUT / f"{set_name}_attribution.json").write_text(json.dumps({k: sorted(v) for k, v in attrib.items()}, indent=1))
        nov = {p: 0 for p in PROVIDERS}
        uniq = {p: 0 for p in PROVIDERS}
        for key, provs in attrib.items():
            q, k = key.split("|", 1)
            if k in KB:
                continue
            for p in provs:
                nov[p] += 1
            if len(provs) == 1:
                uniq[next(iter(provs))] += 1
        summary[f"{set_name}_novel_urls"] = nov
        summary[f"{set_name}_novel_urls_unique_to_provider"] = uniq
        summary[f"{set_name}_pooled_unique_urls"] = sum(len(pl["results"]) for pl in pools)

    # cost and latency
    led = json.loads((RUNS / "ledger.json").read_text())
    summary["ledger"] = led
    lat = {}
    for set_name in ["S1", "S2", "S3", "S4"]:
        for (p, _), r in load(set_name).items():
            lat.setdefault(p, []).append(r["latency_ms"])
    summary["median_latency_ms"] = {p: sorted(v)[len(v) // 2] for p, v in lat.items() if v}
    (OUT / "summary_stageA_scripted.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "S3_recovery"}, indent=1)[:4000])
    print("S3:", {p: f"{v['recovered']}/{v['n']}" for p, v in s3.items()})


if __name__ == "__main__":
    main()
