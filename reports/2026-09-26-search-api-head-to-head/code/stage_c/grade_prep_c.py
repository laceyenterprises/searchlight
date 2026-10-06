#!/usr/bin/env python3
"""Stage C: build the blind grading pools (Amendment 8).

For each SV question it pools every C1 search result (deduplicated by URL) and every C2 research item (each figure and
fact, with its cited URL) from all eight provider-arms. It then shuffles them with a recorded seed, assigns neutral ids
after the shuffle (r1.. for results, c1.. for research items) and writes:
- grading/C/pool_<id>.json: what the judges see, with no provider names;
- grading/C/_attrib/<id>.json: the id-to-provider-arm map, which judges never open.

Usage: python3 grade_prep_c.py [--seed 20260927]
"""
import argparse, hashlib, json, pathlib, random, re, urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent
SET = json.loads((ROOT / "sets" / "SV.json").read_text())
OUT = ROOT / "grading" / "C"
PROVIDERS = ["exa", "tavily", "parallel", "firecrawl"]
TEXT_CHARS = 900


def norm_url(u):
    """Match the same page across providers: drop the fragment, tracking parameters and trailing slashes; lowercase the host."""
    if not u:
        return ""
    p = urllib.parse.urlsplit(u.strip())
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not re.match(r"(utm_|ref$|source$|fbclid|gclid)", k)]
    path = re.sub(r"/+$", "", p.path) or "/"
    return urllib.parse.urlunsplit((p.scheme.lower() or "https", p.netloc.lower().removeprefix("www."), path, urllib.parse.urlencode(q), ""))


def load_jsonl(path):
    return [json.loads(l) for l in open(path, encoding="utf-8")] if path.exists() else []


def c1_records():
    recs = {}
    for p in PROVIDERS:
        for r in load_jsonl(ROOT / "runs" / "SV" / f"{p}.jsonl"):
            if r.get("status") == 200:
                recs[(p, r["id"])] = r  # one run per question; a later record for the same id would replace it
    return recs


def c2_records():
    recs = {}
    for p in PROVIDERS:
        for r in load_jsonl(ROOT / "runs" / "SV-research" / f"{p}.jsonl"):
            ok = r.get("terminal_status") == "completed" and r.get("structured") is not None
            if ok or (p, r["id"]) not in recs:
                recs[(p, r["id"])] = r  # the last completed run per question (Tavily's g4 retry, any logged retries)
    return recs


def structured(r):
    s = r.get("structured")
    if isinstance(s, str):
        try:
            s = json.loads(s)
        except Exception:
            return {}
    return s if isinstance(s, dict) else {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20260927)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "_attrib").mkdir(exist_ok=True)
    c1, c2 = c1_records(), c2_records()
    summary = {}
    for q in SET["items"]:
        qid = q["id"]
        # C1: dedupe by normalized URL, keeping the longest excerpt; attribution lists every provider that returned it
        by_url = {}
        for p in PROVIDERS:
            r = c1.get((p, qid))
            for res in (r or {}).get("results") or []:
                key = norm_url(res.get("url"))
                if not key:
                    continue
                e = by_url.setdefault(key, {"url": res.get("url"), "title": res.get("title"), "published": res.get("published"),
                                            "text": "", "arms": []})
                txt = (res.get("text") or "").strip()
                if len(txt) > len(e["text"]):
                    e["text"] = txt
                if f"{p}:search" not in e["arms"]:
                    e["arms"].append(f"{p}:search")
        items = [{"kind": "result", **{k: v for k, v in e.items() if k != "arms"}, "_arms": e["arms"]} for e in by_url.values()]
        # C2: every figure and fact with a URL, one item each
        runs = {}
        for p in PROVIDERS:
            r = c2.get((p, qid))
            if not r:
                runs[p] = "no run"
                continue
            runs[p] = r.get("terminal_status") or r.get("status")
            s = structured(r)
            for f in s.get("figures") or []:
                items.append({"kind": "figure", "value": f.get("value"), "unit": f.get("unit"), "as_of": f.get("as_of"),
                              "measures": f.get("measures"), "source_type": f.get("source_type"),
                              "quote": (f.get("quote") or "")[:220], "source_url": f.get("source_url"), "_arms": [f"{p}:research"]})
            for f in s.get("other_facts") or []:
                items.append({"kind": "fact", "fact": f.get("fact"), "source_url": f.get("source_url"), "_arms": [f"{p}:research"]})
        seed = a.seed + int(hashlib.sha256(qid.encode()).hexdigest()[:8], 16)
        random.Random(seed).shuffle(items)
        pool, attrib, nr, nc = [], {}, 0, 0
        for it in items:
            if it["kind"] == "result":
                nr += 1
                iid = f"r{nr}"
                pool.append({"rid": iid, "url": it["url"], "title": it["title"], "published": it["published"],
                             "text": (it["text"] or "")[:TEXT_CHARS]})
            else:
                nc += 1
                iid = f"c{nc}"
                pool.append({"cid": iid, **{k: v for k, v in it.items() if k not in ("kind", "_arms")}, "kind": it["kind"]})
            attrib[iid] = it["_arms"]
        (OUT / f"pool_{qid}.json").write_text(json.dumps({"id": qid, "vendor": q["vendor"], "facet": q["facet"], "tier": q["tier"],
                                                          "question": q["question"], "kb_context": q["kb_context"],
                                                          "seed": seed, "items": pool}, ensure_ascii=False, indent=1))
        (OUT / "_attrib" / f"{qid}.json").write_text(json.dumps({"id": qid, "seed": seed, "attrib": attrib, "research_runs": runs}, indent=1))
        summary[qid] = {"results": nr, "research_items": nc, "research_runs": runs}
    (OUT / "_attrib" / "summary.json").write_text(json.dumps(summary, indent=1))
    tot_r = sum(v["results"] for v in summary.values())
    tot_c = sum(v["research_items"] for v in summary.values())
    print(f"pools written for {len(summary)} questions: {tot_r} unique result URLs, {tot_c} research items (seed {a.seed})")


if __name__ == "__main__":
    main()
