#!/usr/bin/env python3
"""Stage C research arm (C2): each provider's research agent answers the SV questions in one shared output schema.

Pre-registered (Amendment 8):
- Exa Agent (effort medium, $0.10 a run), Parallel Task (processor pro, $0.10 a run), Tavily Research (model mini),
  Firecrawl /agent (maxCredits 100). Each gets the same prompt and the same JSON schema.
- Keys come from the environment only (`op run --env-file keys.env -- python3 harness_research.py ...`); they are never
  printed or written. Every run is logged to runs/SV-research/<provider>.jsonl (local only: vendor output isn't
  committed). runs/ledger_c2.json enforces the per-provider caps in config_c2.json, and a total guard keeps the search
  arm plus the research arm within the stage's total cap (credits converted at list rates).

Usage: python3 harness_research.py [--providers exa,parallel,tavily,firecrawl] [--only g1,a1] [--limit N]
"""
import argparse, contextlib, datetime, fcntl, json, os, pathlib, threading, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

import harness as A  # the search arm's helpers (post, parse, Pace), byte-identical to Stage A's harness.py

ROOT = pathlib.Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config_c2.json").read_text())
LEDGER_C1_PATH = ROOT / "runs" / "ledger.json"
LEDGER_PATH = ROOT / "runs" / "ledger_c2.json"
SET_NAME = "SV-research"
LOCK = threading.Lock()
UA = "kb-provider-eval/0.3"
PROVIDERS = ["exa", "parallel", "tavily", "firecrawl"]


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---------- shared ledger with reservations ----------
@contextlib.contextmanager
def ledger_lock():
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(str(LEDGER_PATH) + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _read():
    if LEDGER_PATH.exists():
        return json.loads(LEDGER_PATH.read_text())
    return {"spent": {p: {"usd": 0.0, "credits": 0, "runs": 0} for p in PROVIDERS}, "reserved": {p: 0.0 for p in PROVIDERS}}


def _write(d):
    LEDGER_PATH.write_text(json.dumps(d, indent=2))


def usd_of(provider, usd, credits):
    return (usd or 0.0) + (credits or 0) * CFG["credit_usd"].get(provider, 0.0)


def spent_total_usd(d):
    c1 = json.loads(LEDGER_C1_PATH.read_text()) if LEDGER_C1_PATH.exists() else {}
    a = sum(usd_of(p, v.get("usd", 0.0), v.get("credits", 0)) for p, v in c1.items())
    b = sum(usd_of(p, v.get("usd", 0.0), v.get("credits", 0)) for p, v in d["spent"].items())
    return a + b


def _fits(d, provider, worst_usd, worst_credits):
    cap, led, held = CFG["caps"][provider], d["spent"][provider], d["reserved"][provider]
    cu = CFG["credit_usd"].get(provider, 0.0)
    if "usd" in cap and led["usd"] + held + worst_usd > cap["usd"]:
        return False
    if "credits" in cap and led["credits"] + (held / cu if cu else 0) + worst_credits > cap["credits"]:
        return False
    return spent_total_usd(d) + sum(d["reserved"].values()) + usd_of(provider, worst_usd, worst_credits) <= CFG["total_cap_usd"]


def can_spend(provider, worst_usd=0.0, worst_credits=0):
    """Reserve a run's worst case, or wait (up to guard_wait_minutes) for room; None means the cap refuses it."""
    end = time.time() + CFG["guard_wait_minutes"] * 60
    held = usd_of(provider, worst_usd, worst_credits)
    while True:
        with ledger_lock():
            d = _read()
            if _fits(d, provider, worst_usd, worst_credits):
                d["reserved"][provider] += held
                _write(d)
                return held
        if time.time() >= end:
            return None
        time.sleep(20)


def spend(provider, usd=0.0, credits=0, release=0.0):
    with ledger_lock():
        d = _read()
        d["reserved"][provider] = max(0.0, d["reserved"][provider] - (release or 0.0))
        d["spent"][provider]["usd"] += usd or 0.0
        d["spent"][provider]["credits"] += credits or 0
        d["spent"][provider]["runs"] += 1
        _write(d)


def get(url, headers, timeout=60, tries=4):
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={**headers, "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", "replace")
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(10 * (attempt + 1))
                continue
            return e.code, txt
        except Exception as e:
            if attempt < tries - 1:
                time.sleep(5)
                continue
            return -1, json.dumps({"error": repr(e)})


def log(provider, rec):
    out = ROOT / "runs" / SET_NAME / f"{provider}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with LOCK, open(out, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def deadline():
    return time.time() + CFG["timeout_minutes"] * 60


# ---------- the shared task: one prompt and one schema for every provider ----------
def prompt(item):
    return (f"{item['question']}\n\n"
            "Research this with current public sources, for a market-sizing analyst who needs figures they can cite. "
            "Report every relevant figure you find: its value and unit, the date it refers to, exactly what it measures, "
            "the URL of the page that states it, and whether it is a company statement, a filing or earnings call, a press "
            "report, or an analyst estimate. For each figure, include a short quote (under 25 words) from that page. "
            "Also list other relevant facts (suppliers, named customers, terms), each with the URL that states it. "
            "If nothing is disclosed, say so plainly. Don't estimate or infer figures yourself.")


def schema():
    fig = {"type": "object", "description": "One figure found on a public page", "properties": {
        "value": {"type": "string", "description": "The figure as stated, for example '1 billion' or '$14'"},
        "unit": {"type": "string", "description": "Its unit, for example 'queries a month' or 'USD per 1,000 queries'"},
        "as_of": {"type": "string", "description": "The date or period it refers to"},
        "measures": {"type": "string", "description": "Exactly what the figure measures"},
        "source_url": {"type": "string", "description": "URL of the page that states the figure"},
        "source_type": {"type": "string", "description": "company statement, filing or earnings call, press report, analyst estimate, or other"},
        "quote": {"type": "string", "description": "A short quote (under 25 words) from that page stating the figure"}},
        "required": ["value", "measures", "source_url", "source_type"]}
    fact = {"type": "object", "description": "One other relevant fact", "properties": {
        "fact": {"type": "string", "description": "The fact, in one sentence"},
        "source_url": {"type": "string", "description": "URL of the page that states it"}},
        "required": ["fact", "source_url"]}
    return {"type": "object", "properties": {
        "answer": {"type": "string", "description": "A short answer to the question; say plainly if nothing is disclosed"},
        "figures": {"type": "array", "description": "Every relevant figure found", "items": fig},
        "other_facts": {"type": "array", "description": "Other relevant facts with their sources", "items": fact}},
        "required": ["answer", "figures", "other_facts"]}


def tavily_schema():
    """Tavily Research accepts only 'properties' and 'required' at the top level, and property schemas made of type,
    description, properties and items (Amendment 9). Same fields and descriptions as schema()."""
    def strip(node):
        out = {k: v for k, v in node.items() if k in ("type", "description", "properties", "items")}
        if "properties" in out:
            out["properties"] = {k: strip(v) for k, v in out["properties"].items()}
        if "items" in out:
            out["items"] = strip(out["items"])
        return out
    s = schema()
    return {"properties": {k: strip(v) for k, v in s["properties"].items()}, "required": s["required"]}


# ---------- Exa Agent ----------
def exa_run(item):
    c = CFG["exa"]
    body = {"query": prompt(item), "effort": c["effort"], "outputSchema": schema()}
    held = can_spend("exa", worst_usd=c["price_usd"])
    if held is None:
        return {"status": "skipped_cap", "request": body}
    h = {"Authorization": "Bearer " + os.environ["EXA_API_KEY"]}
    t0 = time.time()
    st, txt, _ = A.post("https://api.exa.ai/agent/runs", h, body, timeout=120, pace=A.PACE["exa"])
    j = A.parse(txt)
    rid = j.get("id") if st in (200, 201) else None
    end = deadline()
    while rid and j.get("status") not in ("completed", "failed", "cancelled") and time.time() < end:
        time.sleep(CFG["poll_seconds"]["exa"])
        s2, t2 = get(f"https://api.exa.ai/agent/runs/{rid}", h)
        if s2 == 200:
            j = A.parse(t2)
    if rid and j.get("status") not in ("completed", "failed", "cancelled"):
        A.post(f"https://api.exa.ai/agent/runs/{rid}/cancel", h, {}, timeout=60)
        s2, t2 = get(f"https://api.exa.ai/agent/runs/{rid}", h)
        j = A.parse(t2) if s2 == 200 else j
    usd = ((j.get("costDollars") or {}).get("total") or 0.0) if rid else 0.0
    spend("exa", usd=usd, release=held)
    out = j.get("output") or {}
    return {"status": st, "run_id": rid, "terminal_status": j.get("status"), "latency_ms": int((time.time() - t0) * 1000),
            "cost": {"usd": usd, "breakdown": j.get("costDollars")}, "request": body,
            "structured": out.get("structured"), "citations": out.get("grounding"), "raw": j}


# ---------- Parallel Task ----------
def parallel_run(item):
    c = CFG["parallel"]
    body = {"input": prompt(item), "processor": c["processor"],
            "task_spec": {"output_schema": {"type": "json", "json_schema": schema()}}}
    held = can_spend("parallel", worst_usd=c["price_usd"])
    if held is None:
        return {"status": "skipped_cap", "request": body}
    h = {"x-api-key": os.environ["PARALLEL_API_KEY"]}
    t0 = time.time()
    st, txt, _ = A.post("https://api.parallel.ai/v1/tasks/runs", h, body, timeout=120, pace=A.PACE["parallel"])
    j = A.parse(txt)
    rid = j.get("run_id") if st in (200, 201, 202) else None
    run, end = j, deadline()
    while rid and time.time() < end:
        time.sleep(CFG["poll_seconds"]["parallel"])
        s2, t2 = get(f"https://api.parallel.ai/v1/tasks/runs/{rid}", h)
        if s2 == 200:
            run = A.parse(t2)
            if run.get("status") in ("completed", "failed", "cancelled"):
                break
    result = {}
    if rid:
        s3, t3 = get(f"https://api.parallel.ai/v1/tasks/runs/{rid}/result", h, timeout=120)
        result = A.parse(t3) if s3 == 200 else {"_status": s3, "_body": t3[:500]}
    ok = run.get("status") == "completed"
    usd = c["price_usd"] if ok else 0.0
    spend("parallel", usd=usd, release=held)
    out = result.get("output") or {}
    return {"status": st, "run_id": rid, "terminal_status": run.get("status"), "latency_ms": int((time.time() - t0) * 1000),
            "cost": {"usd": usd, "basis": "list price per successful run"}, "request": body,
            "structured": out.get("content"), "citations": out.get("basis"), "raw": result}


# ---------- Tavily Research ----------
def tavily_run(item):
    c = CFG["tavily"]
    body = {"input": prompt(item), "model": c["model"], "output_schema": tavily_schema(), "citation_format": "numbered",
            "output_length": c["output_length"]}
    held = can_spend("tavily", worst_credits=c["worst_credits"])
    if held is None:
        return {"status": "skipped_cap", "request": body}
    h = {"Authorization": "Bearer " + os.environ["TAVILY_API_KEY"]}
    t0 = time.time()
    st, txt, _ = A.post("https://api.tavily.com/research", h, body, timeout=120, pace=A.PACE["tavily"])
    j = A.parse(txt)
    rid = j.get("request_id") if st in (200, 201, 202) else None
    run, end = j, deadline()
    while rid and time.time() < end:
        time.sleep(CFG["poll_seconds"]["tavily"])
        s2, t2 = get(f"https://api.tavily.com/research/{rid}?include_usage=true", h)
        if s2 == 200:
            run = A.parse(t2)
            if run.get("status") in ("completed", "failed"):
                break
    credits = ((run.get("usage") or {}).get("credits")) if rid else 0
    if rid and run.get("status") == "completed" and credits is None:
        credits = c["worst_credits"]  # unreported usage is charged at the worst case, not zero
    spend("tavily", credits=credits or 0, release=held)
    content = run.get("content")
    return {"status": st, "run_id": rid, "terminal_status": run.get("status"), "latency_ms": int((time.time() - t0) * 1000),
            "cost": {"credits": credits, "usage": run.get("usage")}, "request": body,
            "structured": content if isinstance(content, dict) else None,
            "report": content if isinstance(content, str) else None, "citations": run.get("sources"), "raw": run}


# ---------- Firecrawl /agent ----------
def fc_remaining(h):
    st, txt = get("https://api.firecrawl.dev/v2/team/credit-usage", h)
    return (A.parse(txt).get("data") or {}).get("remainingCredits") if st == 200 else None


def firecrawl_run(item):
    c = CFG["firecrawl"]
    body = {"prompt": prompt(item), "schema": schema(), "maxCredits": c["max_credits"]}
    h = {"Authorization": "Bearer " + os.environ["FIRECRAWL_API_KEY"]}
    with ledger_lock():
        led = _read()["spent"]["firecrawl"]
    paid = led["runs"] >= c["free_runs_per_day"] or led["credits"] > 0
    held = 0.0
    if paid:
        held = can_spend("firecrawl", worst_credits=c["max_credits"])
        if held is None:
            return {"status": "skipped_cap", "request": body}
    before = fc_remaining(h)
    t0 = time.time()
    st, txt, _ = A.post("https://api.firecrawl.dev/v2/agent", h, body, timeout=120, pace=A.PACE["firecrawl"])
    j = A.parse(txt)
    jid = j.get("id") if st == 200 else None
    run, end = j, deadline()
    while jid and time.time() < end:
        time.sleep(CFG["poll_seconds"]["firecrawl"])
        s2, t2 = get(f"https://api.firecrawl.dev/v2/agent/{jid}", h)
        if s2 == 200:
            run = A.parse(t2)
            if run.get("status") != "processing":
                break
    after = fc_remaining(h)
    delta = (before - after) if (before is not None and after is not None) else None
    reported = run.get("creditsUsed")
    billed = (reported or 0) if paid else max(delta or 0, 0)
    spend("firecrawl", credits=billed, release=held)
    return {"status": st, "run_id": jid, "terminal_status": run.get("status"), "latency_ms": int((time.time() - t0) * 1000),
            "cost": {"credits_used_reported": reported, "credits_billed": billed, "paid_run": paid,
                     "note": "the first 5 agent runs a day are free"},
            "request": body, "structured": run.get("data"), "citations": None, "raw": run}


RUN = {"exa": exa_run, "parallel": parallel_run, "tavily": tavily_run, "firecrawl": firecrawl_run}


def one(provider, item):
    try:
        rec = RUN[provider](item)
    except KeyError as e:
        rec = {"status": "missing_env", "error": str(e)}
    except Exception as e:  # keep the other runs going; the record says what failed
        rec = {"status": "error", "error": repr(e)[:500]}
    rec.update({"set": SET_NAME, "id": item["id"], "provider": provider, "ts": now()})
    log(provider, rec)
    print(f"[{provider}] {item['id']} status={rec.get('status')} terminal={rec.get('terminal_status')} "
          f"{rec.get('latency_ms')}ms cost={rec.get('cost')}", flush=True)
    return rec


def run_provider(provider, items):
    with ThreadPoolExecutor(max_workers=CFG["concurrency"][provider]) as ex:
        list(ex.map(lambda it: one(provider, it), items))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", default=",".join(PROVIDERS))
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    items = json.loads((ROOT / "sets" / "SV.json").read_text())["items"]
    if a.only:
        keep = set(a.only.split(","))
        items = [i for i in items if i["id"] in keep]
    if a.limit:
        items = items[:a.limit]
    threads = [threading.Thread(target=run_provider, args=(p, items)) for p in a.providers.split(",") if p]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with ledger_lock():
        d = _read()
    print("LEDGER_C2", json.dumps(d["spent"]), "TOTAL_USD", round(spent_total_usd(d), 4), flush=True)


if __name__ == "__main__":
    main()
