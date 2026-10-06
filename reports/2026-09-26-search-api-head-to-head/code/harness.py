#!/usr/bin/env python3
"""Provider-eval harness: runs a pre-registered query set against Exa, Tavily, Parallel, and Firecrawl.

Keys come from the environment only (inject with `op run --env-file keys.env -- python3 harness.py ...`).
Keys are never printed or written. Every call is logged to runs/<set>/<provider>.jsonl with cost and latency,
and a shared ledger (runs/ledger.json) enforces the per-provider hard caps in config.json.

Usage: python3 harness.py --set S1 [--limit 20] [--offset 0] [--providers exa,tavily,parallel,firecrawl]
"""
import argparse, json, os, pathlib, threading, time, urllib.error, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text())
LEDGER_PATH = ROOT / "runs" / "ledger.json"
LOCK = threading.Lock()


def load_ledger():
    if LEDGER_PATH.exists():
        return json.loads(LEDGER_PATH.read_text())
    return {p: {"usd": 0.0, "credits": 0, "calls": 0} for p in CFG["caps"]}


LEDGER = load_ledger()


def spend(provider, usd=0.0, credits=0):
    with LOCK:
        LEDGER[provider]["usd"] += usd or 0.0
        LEDGER[provider]["credits"] += credits or 0
        LEDGER[provider]["calls"] += 1
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        LEDGER_PATH.write_text(json.dumps(LEDGER, indent=2))


def over_cap(provider):
    cap = CFG["caps"][provider]
    led = LEDGER[provider]
    return led["usd"] >= cap.get("usd", 1e9) or led["credits"] >= cap.get("credits", 1e12)


def post(url, headers, body, timeout=120, pace=None, tries=4):
    data = json.dumps(body).encode()
    for attempt in range(tries):
        if pace:
            pace.wait()
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={**headers, "Content-Type": "application/json", "User-Agent": "kb-provider-eval/0.1"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace"), int((time.time() - t0) * 1000)
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", "replace")
            if e.code in (429, 502, 503) and attempt < tries - 1:
                time.sleep(8 * (attempt + 1))
                continue
            return e.code, txt, int((time.time() - t0) * 1000)
        except Exception as e:  # network errors, timeouts
            if attempt < tries - 1:
                time.sleep(5)
                continue
            return -1, json.dumps({"error": repr(e)}), int((time.time() - t0) * 1000)


class Pace:
    """Minimum spacing between requests for one provider (rate-limit courtesy)."""
    def __init__(self, seconds):
        self.s, self.t, self.lock = seconds, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            dt = self.t + self.s - time.time()
            if dt > 0:
                time.sleep(dt)
            self.t = time.time()


PACE = {p: Pace(CFG["pace_seconds"][p]) for p in CFG["pace_seconds"]}


def trim(s, n=2000):
    s = " ".join((s or "").split())
    return s[:n]


def parse(txt):
    try:
        return json.loads(txt)
    except Exception:
        return {"_unparsed": txt[:2000]}


# ---------- adapters: search ----------
def exa_search(item, kind):
    body = {"query": item["question"], "type": "auto", "numResults": 10, "contents": {"highlights": True}}
    if kind == "fresh":
        body.update({"category": "news", "startPublishedDate": CFG["fresh_start"] + "T00:00:00.000Z"})
    st, txt, ms = post("https://api.exa.ai/search", {"Authorization": "Bearer " + os.environ["EXA_API_KEY"]}, body, pace=PACE["exa"])
    j = parse(txt)
    res = [{"rank": i + 1, "url": r.get("url"), "title": r.get("title"), "published": r.get("publishedDate"),
            "text": trim(" … ".join(r.get("highlights") or []))} for i, r in enumerate(j.get("results", []) if st == 200 else [])]
    usd = (j.get("costDollars") or {}).get("total") if st == 200 else 0.0
    spend("exa", usd=usd or 0.0)
    return {"status": st, "latency_ms": ms, "cost": {"usd": usd}, "results": res, "request": body, "raw": j}


def tavily_search(item, kind):
    body = {"query": item["question"], "search_depth": "advanced", "max_results": 10, "chunks_per_source": 3, "include_usage": True}
    if kind == "fresh":
        body.update({"topic": "news", "start_date": CFG["fresh_start"]})
    st, txt, ms = post("https://api.tavily.com/search", {"Authorization": "Bearer " + os.environ["TAVILY_API_KEY"]}, body, pace=PACE["tavily"])
    j = parse(txt)
    res = [{"rank": i + 1, "url": r.get("url"), "title": r.get("title"), "published": r.get("published_date"),
            "text": trim(r.get("content"))} for i, r in enumerate(j.get("results", []) if st == 200 else [])]
    credits = ((j.get("usage") or {}).get("credits")) if st == 200 else 0
    if st == 200 and credits is None:
        credits = 2
    spend("tavily", credits=credits or 0)
    return {"status": st, "latency_ms": ms, "cost": {"credits": credits}, "results": res, "request": body, "raw": j}


def parallel_search(item, kind):
    body = {"objective": item["question"], "search_queries": item["queries"][:3], "mode": "advanced",
            "advanced_settings": {"max_results": 10}}
    if kind == "fresh":
        body["advanced_settings"]["source_policy"] = {"after_date": CFG["fresh_start"]}
    st, txt, ms = post("https://api.parallel.ai/v1/search", {"x-api-key": os.environ["PARALLEL_API_KEY"]}, body, pace=PACE["parallel"])
    j = parse(txt)
    res = [{"rank": i + 1, "url": r.get("url"), "title": r.get("title"), "published": r.get("publish_date"),
            "text": trim(" … ".join(r.get("excerpts") or []))} for i, r in enumerate(j.get("results", []) if st == 200 else [])]
    usd = CFG["unit_prices"]["parallel_search_advanced"] if st == 200 else 0.0
    spend("parallel", usd=usd)
    return {"status": st, "latency_ms": ms, "cost": {"usd": usd, "usage": j.get("usage")}, "results": res, "request": body, "raw": j}


def firecrawl_scrape_url(url, timeout_ms=30000, pdf_max_pages=None):
    body = {"url": url, "formats": ["markdown"], "onlyMainContent": True, "proxy": "auto", "timeout": timeout_ms}
    if pdf_max_pages:
        body["parsers"] = [{"type": "pdf", "maxPages": pdf_max_pages}]
    st, txt, ms = post("https://api.firecrawl.dev/v2/scrape", {"Authorization": "Bearer " + os.environ["FIRECRAWL_API_KEY"]}, body, pace=PACE["firecrawl"])
    j = parse(txt)
    data = j.get("data") or {}
    credits = data.get("metadata", {}).get("creditsUsed") or j.get("creditsUsed") or (1 if st == 200 else 0)
    spend("firecrawl", credits=credits)
    return st, ms, data.get("markdown") or "", credits, j


def firecrawl_search(item, kind):
    body = {"query": item["keyword_query"], "limit": 10}
    if kind == "fresh":
        m, d, y = CFG["fresh_start_mdy"]
        e_m, e_d, e_y = CFG["fresh_end_mdy"]
        body["tbs"] = f"cdr:1,cd_min:{m}/{d}/{y},cd_max:{e_m}/{e_d}/{e_y}"
    st, txt, ms = post("https://api.firecrawl.dev/v2/search", {"Authorization": "Bearer " + os.environ["FIRECRAWL_API_KEY"]}, body, pace=PACE["firecrawl"])
    j = parse(txt)
    data = j.get("data") or {}
    rows = (data.get("web") or []) + (data.get("news") or []) if isinstance(data, dict) else (data or [])
    credits = j.get("creditsUsed") or (2 if st == 200 else 0)
    spend("firecrawl", credits=credits)
    res = [{"rank": i + 1, "url": r.get("url"), "title": r.get("title"), "published": r.get("date"),
            "text": trim(r.get("description") or r.get("snippet"))} for i, r in enumerate(rows[:10])]
    scrape_n = CFG["firecrawl_scrape_top"] if kind == "search" else 0
    total_credits = credits
    for r in res[:scrape_n]:
        if over_cap("firecrawl"):
            break
        host = (r.get("url") or "").split("/")[2].lower() if "//" in (r.get("url") or "") else ""
        if any(host == d or host.endswith("." + d) for d in CFG["firecrawl_skip_domains"]):
            r["scrape_skipped"] = "premium or login-walled domain"
            continue
        sst, sms, md, c, _ = firecrawl_scrape_url(r["url"], pdf_max_pages=CFG["firecrawl_pdf_max_pages"])
        total_credits += c
        if sst == 200 and md:
            r["text"] = trim(md, 2000)
            r["scraped"] = True
    return {"status": st, "latency_ms": ms, "cost": {"credits": total_credits}, "results": res, "request": body, "raw": j}


# ---------- adapters: fetch (S3) ----------
def exa_fetch(item):
    body = {"ids": [item["url"]], "text": {"maxCharacters": 20000}, "maxAgeHours": 0, "livecrawlTimeout": 15000}
    st, txt, ms = post("https://api.exa.ai/contents", {"Authorization": "Bearer " + os.environ["EXA_API_KEY"]}, body, pace=PACE["exa"])
    j = parse(txt)
    r = (j.get("results") or [{}])[0] if st == 200 else {}
    usd = (j.get("costDollars") or {}).get("total") if st == 200 else 0.0
    spend("exa", usd=usd or 0.0)
    return {"status": st, "latency_ms": ms, "cost": {"usd": usd}, "content": r.get("text") or "", "detail": j.get("statuses"), "request": body}


def tavily_fetch(item):
    body = {"urls": [item["url"]], "extract_depth": "advanced", "format": "markdown", "include_usage": True}
    st, txt, ms = post("https://api.tavily.com/extract", {"Authorization": "Bearer " + os.environ["TAVILY_API_KEY"]}, body, pace=PACE["tavily"])
    j = parse(txt)
    r = (j.get("results") or [{}])[0] if st == 200 else {}
    credits = ((j.get("usage") or {}).get("credits")) if st == 200 else 0
    if st == 200 and credits is None:
        credits = 2
    spend("tavily", credits=credits or 0)
    return {"status": st, "latency_ms": ms, "cost": {"credits": credits}, "content": r.get("raw_content") or "", "detail": j.get("failed_results"), "request": body}


def parallel_fetch(item):
    body = {"urls": [item["url"]], "objective": item["target_desc"], "advanced_settings": {"full_content": True}}
    st, txt, ms = post("https://api.parallel.ai/v1/extract", {"x-api-key": os.environ["PARALLEL_API_KEY"]}, body, pace=PACE["parallel"])
    j = parse(txt)
    r = (j.get("results") or [{}])[0] if st == 200 else {}
    usd = CFG["unit_prices"]["parallel_extract"] if st == 200 else 0.0
    spend("parallel", usd=usd)
    content = r.get("full_content") or " ".join(r.get("excerpts") or [])
    return {"status": st, "latency_ms": ms, "cost": {"usd": usd}, "content": content, "detail": j.get("errors"), "request": body}


def firecrawl_fetch(item):
    st, ms, md, credits, j = firecrawl_scrape_url(item["url"], timeout_ms=45000)
    return {"status": st, "latency_ms": ms, "cost": {"credits": credits}, "content": md, "detail": (j.get("error") if st != 200 else None),
            "request": {"url": item["url"], "formats": ["markdown"], "proxy": "auto"}}


SEARCH = {"exa": exa_search, "tavily": tavily_search, "parallel": parallel_search, "firecrawl": firecrawl_search}
FETCH = {"exa": exa_fetch, "tavily": tavily_fetch, "parallel": parallel_fetch, "firecrawl": firecrawl_fetch}


def run_provider(provider, set_name, items, kind):
    out_dir = ROOT / "runs" / set_name
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(exist_ok=True)
    out = open(out_dir / f"{provider}.jsonl", "a", encoding="utf-8")
    done = 0
    for item in items:
        if over_cap(provider):
            print(f"[{provider}] cap reached; stopping", flush=True)
            break
        try:
            rec = FETCH[provider](item) if kind == "fetch" else SEARCH[provider](item, kind)
        except KeyError as e:
            print(f"[{provider}] missing env var {e}; stopping", flush=True)
            break
        raw = rec.pop("raw", None)
        if raw is not None:
            (raw_dir / f"{provider}-{item['id']}.json").write_text(json.dumps(raw)[:500000])
        rec.update({"set": set_name, "id": item["id"], "provider": provider, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
        if kind == "fetch":
            rec["content_chars"] = len(rec.get("content") or "")
            rec["content"] = (rec.get("content") or "")[:30000]
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out.flush()
        done += 1
        print(f"[{provider}] {set_name}/{item['id']} status={rec['status']} {rec['latency_ms']}ms cost={rec['cost']}", flush=True)
    out.close()
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--providers", default="exa,tavily,parallel,firecrawl")
    a = ap.parse_args()
    spec = json.loads((ROOT / "sets" / f"{a.set}.json").read_text())
    items = spec["items"][a.offset:]
    if a.limit:
        items = items[:a.limit]
    kind = spec["kind"]
    provs = [p for p in a.providers.split(",") if p]
    threads = [threading.Thread(target=run_provider, args=(p, a.set, items, kind)) for p in provs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print("LEDGER", json.dumps(LEDGER), flush=True)


if __name__ == "__main__":
    main()
