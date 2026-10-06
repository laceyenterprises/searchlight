#!/usr/bin/env python3
"""Post-hoc Stage A diagnostics (Amendment 4), run after the PR #25 review. They add evidence and change no set.

D1  Exa /contents with its default cache behavior (no maxAgeHours, no livecrawlTimeout) on the 5 S3 pages whose
    forced live crawl errored (b05, b10, b11, b12, b15).
D2  S2 re-run with identical inputs: every provider gets only the question text. Parallel's queries and Firecrawl's
    keyword query are set to the question, removing the hand-written queries that leaked answer terms.
D3  Firecrawl's date filter: S4 f01 with and without `tbs`, to see whether the filter changes the results.

Usage: op run --env-file keys.env -- python3 diag_stageA.py D1|D2|D3
Outputs go to runs/diag/ (vendor output stays local, like runs/). Costs are counted in runs/ledger.json.
"""
import json, os, pathlib, sys

import harness as H

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "runs" / "diag"
OUT.mkdir(parents=True, exist_ok=True)


def write(name, rec):
    with open(OUT / name, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def d1():
    items = {i["id"]: i for i in json.loads((ROOT / "sets" / "S3.json").read_text())["items"]}
    for bid in ["b05", "b10", "b11", "b12", "b15"]:
        it = items[bid]
        body = {"ids": [it["url"]], "text": {"maxCharacters": 20000}}
        st, txt, ms = H.post("https://api.exa.ai/contents", {"Authorization": "Bearer " + os.environ["EXA_API_KEY"]}, body, pace=H.PACE["exa"])
        j = H.parse(txt)
        r = (j.get("results") or [{}])[0] if st == 200 else {}
        usd = ((j.get("costDollars") or {}).get("total") or 0.0) if st == 200 else 0.0
        H.spend("exa", usd=usd)
        c = r.get("text") or ""
        hit = [ph for ph in it["target_phrases"] if ph.lower() in c.lower()]
        rec = {"diag": "D1", "id": bid, "status": st, "latency_ms": ms, "cost": {"usd": usd}, "content_chars": len(c),
               "phrases_found": hit, "recovered": st == 200 and len(c) >= 500 and bool(hit), "detail": j.get("statuses"),
               "request": body, "content": c}
        write("D1_exa_contents_default.jsonl", rec)
        print(bid, st, len(c), hit, rec["recovered"], json.dumps(j.get("statuses"))[:160])


def d2():
    items = json.loads((ROOT / "sets" / "S2.json").read_text())["items"]
    for it in items:
        same = {**it, "queries": [it["question"]], "keyword_query": it["question"]}
        for p, fn in [("exa", H.exa_search), ("tavily", H.tavily_search), ("parallel", H.parallel_search), ("firecrawl", H.firecrawl_search)]:
            if H.over_cap(p):
                print("cap", p)
                continue
            rec = fn(same, "search")
            rec.update({"diag": "D2", "id": it["id"], "provider": p})
            write(f"D2_S2_identical_{p}.jsonl", rec)
            print(it["id"], p, rec["status"], len(rec["results"]))


def d3():
    it = json.loads((ROOT / "sets" / "S4.json").read_text())["items"][0]
    for kind in ["fresh", "search_no_scrape"]:
        k = "fresh" if kind == "fresh" else "nofilter"
        body_kind = "fresh" if kind == "fresh" else "fresh_off"
        if body_kind == "fresh":
            rec = H.firecrawl_search(it, "fresh")
        else:
            saved = H.CFG["firecrawl_scrape_top"]
            H.CFG["firecrawl_scrape_top"] = 0  # no scrapes: compare search results only
            rec = H.firecrawl_search(it, "search")
            H.CFG["firecrawl_scrape_top"] = saved
        rec.update({"diag": "D3", "id": it["id"], "variant": k})
        write("D3_firecrawl_tbs.jsonl", rec)
        print(k, rec["status"], [(r["url"][:70], r.get("published")) for r in rec["results"][:10]])


if __name__ == "__main__":
    {"D1": d1, "D2": d2, "D3": d3}[sys.argv[1]]()
    print("LEDGER", json.dumps(H.LEDGER))
