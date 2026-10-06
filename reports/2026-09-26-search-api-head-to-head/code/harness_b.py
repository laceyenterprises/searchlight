#!/usr/bin/env python3
"""Stage B harness: entity lists (S5), schema fill (S6), and Exa's find-similar probe (SP).

Pre-registered capability matrix (Tavily sits out; it sells no list-building or structured-extraction product):
- S5 lists:  Exa Agent (effort auto, default $5 cap), Parallel FindAll (generator core, match_limit 20, base enrichment),
             Firecrawl /agent (maxCredits 2,500; the first 5 runs a day are free).
- S6 schema: Exa Agent (effort medium, one run per company), Parallel Task (processor pro, one run per company),
             Firecrawl /agent (5 companies per run, maxCredits 1,000). Exa and Parallel are price-matched at $0.10 per company.
- SP probe:  Exa /findSimilar on 10 competitor homepages.

Keys come from the environment only (`op run --env-file keys.env -- python3 harness_b.py ...`); they are never printed
or written. Every run is logged to runs/<set>/<provider>.jsonl. runs/ledger_b.json enforces the per-provider caps in
config_b.json, and a total guard keeps Stage A plus Stage B within the owner's cap (credits converted at list rates).

Usage: python3 harness_b.py --set S5,S6 | SP [--providers exa,parallel,firecrawl] [--only S5a,S5b | c01,c02]
"""
import argparse, datetime, json, os, pathlib, threading, time, urllib.error, urllib.request

import harness as A  # Stage A helpers (post, parse, Pace); imported, not modified

ROOT = pathlib.Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config_b.json").read_text())
LEDGER_A = json.loads((ROOT / "runs" / "ledger.json").read_text())
LEDGER_B_PATH = ROOT / "runs" / "ledger_b.json"
LOCK = threading.Lock()
UA = "kb-provider-eval/0.2"
TERMINAL_EXA = {"completed", "failed", "cancelled"}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


import fcntl, contextlib


@contextlib.contextmanager
def ledger_lock():
    """File lock so several harness processes can share one ledger (spend plus in-flight reservations)."""
    LEDGER_B_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(str(LEDGER_B_PATH) + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _read():
    if LEDGER_B_PATH.exists():
        d = json.loads(LEDGER_B_PATH.read_text())
        if "spent" in d:
            return d
        return {"spent": d, "reserved": {p: 0.0 for p in CFG["caps_b"]}}
    return {"spent": {p: {"usd": 0.0, "credits": 0, "runs": 0} for p in CFG["caps_b"]}, "reserved": {p: 0.0 for p in CFG["caps_b"]}}


def _write(d):
    LEDGER_B_PATH.write_text(json.dumps(d, indent=2))


def spent_total_usd(d=None):
    d = d or _read()
    cu = CFG["credit_usd"]
    a = sum(v.get("usd", 0.0) for v in LEDGER_A.values())
    a += LEDGER_A.get("tavily", {}).get("credits", 0) * cu["tavily"] + LEDGER_A.get("firecrawl", {}).get("credits", 0) * cu["firecrawl"]
    b = sum(v.get("usd", 0.0) for v in d["spent"].values()) + d["spent"]["firecrawl"].get("credits", 0) * cu["firecrawl"]
    return a + b


def _fits(d, provider, worst_usd, worst_credits):
    cap, led = CFG["caps_b"][provider], d["spent"][provider]
    cu = CFG["credit_usd"]["firecrawl"]
    held = d["reserved"][provider]
    if "usd" in cap and led["usd"] + held + worst_usd > cap["usd"]:
        return False
    if "credits" in cap and led["credits"] + held / cu + worst_credits > cap["credits"]:
        return False
    return spent_total_usd(d) + sum(d["reserved"].values()) + worst_usd + worst_credits * cu <= CFG["total_cap_usd"]


def can_spend(provider, worst_usd=0.0, worst_credits=0, wait=False):
    """Reserve a run's worst-case cost in the shared ledger, or refuse it if that would break the provider cap or the
    owner's total cap. With wait=True, wait (up to guard_wait_minutes) for in-flight runs to settle and free room."""
    end = time.time() + (CFG.get("guard_wait_minutes", 0) * 60 if wait else 0)
    held = worst_usd + worst_credits * CFG["credit_usd"]["firecrawl"]
    while True:
        with ledger_lock():
            d = _read()
            if _fits(d, provider, worst_usd, worst_credits):
                d["reserved"][provider] += held
                _write(d)
                return held
        if time.time() >= end:
            return None
        time.sleep(30)


def spend(provider, usd=0.0, credits=0, release=0.0):
    with ledger_lock():
        d = _read()
        d["reserved"][provider] = max(0.0, d["reserved"][provider] - (release or 0.0))
        d["spent"][provider]["usd"] += usd or 0.0
        d["spent"][provider]["credits"] += credits or 0
        d["spent"][provider]["runs"] += 1
        _write(d)


def ledger_view():
    with ledger_lock():
        return _read()


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


def log(set_name, provider, rec):
    out = ROOT / "runs" / set_name / f"{provider}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with LOCK, open(out, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def deadline():
    return time.time() + CFG["timeout_minutes"] * 60


# ---------- shared prompt pieces ----------
def list_prompt(spec, n, exclusions=None):
    crit = "\n".join(f"{i + 1}. {c['description']}" for i, c in enumerate(spec["criteria"]))
    fields = "\n".join(f"- {k}: {v}" for k, v in spec["fields"].items())
    p = (f"{spec['objective']}\n\nInclude an entity only if it meets every criterion:\n{crit}\n\n"
         f"Return up to {n} entities. Precision matters more than count: if fewer than {n} qualify, return fewer. "
         f"Verify each entity against every criterion on a public page, preferring the entity's own pages as evidence.\n\n"
         f"Fields for each entity:\n{fields}")
    if exclusions is not None:
        ex = "; ".join(f"{e['name']} ({e['url']})" for e in exclusions)
        p += f"\n\nExclude these already-known entities and their aliases: {ex}"
    return p


def list_schema(spec, n, with_max=True):
    props = {k: {"type": ["string", "null"], "description": v} for k, v in spec["fields"].items()}
    arr = {"type": "array", "items": {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}}
    if with_max:
        arr["maxItems"] = n
    return {"type": "object", "properties": {"entities": arr}, "required": ["entities"], "additionalProperties": False}


def row_schema(s6, strict=False):
    props = {"company": {"type": "string", "description": "The company named in the input"}}
    for f, d in s6["fields"].items():
        props[f] = {"type": ["string", "null"], "description": d}
        props[f + "_source_url"] = {"type": ["string", "null"], "description": f"URL of the page that supports {f}"}
    sch = {"type": "object", "properties": props, "required": list(props) if strict else ["company"], "additionalProperties": False}
    return sch


# ---------- Exa ----------
def exa_headers():
    return {"Authorization": "Bearer " + os.environ["EXA_API_KEY"]}


def exa_agent(body, set_name, item_id, worst_usd):
    held = can_spend("exa", worst_usd=worst_usd)
    if held is None:
        rec = {"set": set_name, "id": item_id, "provider": "exa", "ts": now(), "status": "skipped_cap", "request": body}
        log(set_name, "exa", rec)
        return rec
    t0 = time.time()
    st, txt, _ = A.post("https://api.exa.ai/agent/runs", exa_headers(), body, timeout=120, pace=A.PACE["exa"])
    j = A.parse(txt)
    run_id = j.get("id") if st in (200, 201) else None
    end = deadline()
    while run_id and j.get("status") not in TERMINAL_EXA and time.time() < end:
        time.sleep(CFG["poll_seconds"]["exa"])
        s2, t2 = get(f"https://api.exa.ai/agent/runs/{run_id}", exa_headers())
        if s2 == 200:
            j = A.parse(t2)
    if run_id and j.get("status") not in TERMINAL_EXA:
        A.post(f"https://api.exa.ai/agent/runs/{run_id}/cancel", exa_headers(), {}, timeout=60)
        s2, t2 = get(f"https://api.exa.ai/agent/runs/{run_id}", exa_headers())
        j = A.parse(t2) if s2 == 200 else j
    usd = ((j.get("costDollars") or {}).get("total") or 0.0) if run_id else 0.0
    spend("exa", usd=usd, release=held)
    out = j.get("output") or {}
    rec = {"set": set_name, "id": item_id, "provider": "exa", "ts": now(), "status": st, "run_id": run_id,
           "terminal_status": j.get("status"), "stop_reason": j.get("stopReason"), "latency_ms": int((time.time() - t0) * 1000),
           "cost": {"usd": usd, "breakdown": j.get("costDollars"), "usage": j.get("usage")}, "request": body,
           "structured": out.get("structured"), "grounding": out.get("grounding"), "raw": j}
    log(set_name, "exa", rec)
    return rec


def exa_s5(spec, excl, n):
    body = {"query": list_prompt(spec, n),
            "systemPrompt": "Return only entities you verified against every criterion on a public page. Don't return entities from input.exclusion or aliases of them.",
            "effort": CFG["exa"]["list_effort"], "budget": {"maxCostDollars": CFG["exa"]["list_max_cost_usd"]},
            "outputSchema": list_schema(spec, n), "input": {"exclusion": excl}}
    return exa_agent(body, "S5", spec["id"], CFG["exa"]["list_max_cost_usd"])


def exa_s6(s6, row):
    body = {"query": f"Research the company {row['name']} ({row['homepage']}) and fill every field of the output schema. {s6['instructions']}",
            "systemPrompt": "Judge only the company in input.data. A value must be supported by its cited page; otherwise return null.",
            "effort": CFG["exa"]["row_effort"], "outputSchema": row_schema(s6),
            "input": {"data": [{"company": row["name"], "homepage": row["homepage"]}]}}
    return exa_agent(body, "S6", row["id"], CFG["exa"]["row_price_usd"])


def exa_similar(item, n):
    held = can_spend("exa", worst_usd=0.02)
    if held is None:
        rec = {"set": "SP", "id": item["id"], "provider": "exa", "ts": now(), "status": "skipped_cap"}
        log("SP", "exa", rec)
        return rec
    body = {"url": item["url"], "numResults": n, "excludeSourceDomain": True}
    st, txt, ms = A.post("https://api.exa.ai/findSimilar", exa_headers(), body, pace=A.PACE["exa"])
    j = A.parse(txt)
    usd = ((j.get("costDollars") or {}).get("total") or 0.0) if st == 200 else 0.0
    spend("exa", usd=usd, release=held)
    res = [{"rank": i + 1, "url": r.get("url"), "title": r.get("title"), "published": r.get("publishedDate")}
           for i, r in enumerate(j.get("results", []) if st == 200 else [])]
    rec = {"set": "SP", "id": item["id"], "provider": "exa", "ts": now(), "status": st, "latency_ms": ms,
           "cost": {"usd": usd}, "request": body, "results": res, "raw": j}
    log("SP", "exa", rec)
    return rec


# ---------- Parallel ----------
def par_headers():
    return {"x-api-key": os.environ["PARALLEL_API_KEY"]}


def par_s5(spec, excl, n):
    pc = CFG["parallel"]
    worst = pc["findall_fixed_usd"] + n * (pc["findall_per_match_usd"] + pc["enrich_per_match_usd"])
    body = {"objective": f"{spec['objective']} Up to {n}.", "entity_type": spec["entity_type"],
            "match_conditions": spec["criteria"], "generator": pc["findall_generator"], "match_limit": n,
            "exclude_list": excl}
    held = can_spend("parallel", worst_usd=worst)
    if held is None:
        rec = {"set": "S5", "id": spec["id"], "provider": "parallel", "ts": now(), "status": "skipped_cap", "request": body}
        log("S5", "parallel", rec)
        return rec
    t0 = time.time()
    st, txt, _ = A.post("https://api.parallel.ai/v1beta/findall/runs", par_headers(), body, timeout=120, pace=A.PACE["parallel"])
    j = A.parse(txt)
    fid = j.get("findall_id") if st in (200, 201) else None
    enrich_fields = {k: v for k, v in spec["fields"].items() if k not in ("name", "url")}
    enrich_body = {"processor": pc["enrich_processor"], "output_schema": {"type": "json", "json_schema": {
        "type": "object", "properties": {k: {"type": ["string", "null"], "description": v} for k, v in enrich_fields.items()},
        "required": list(enrich_fields), "additionalProperties": False}}}
    est = None
    if fid:
        est, etxt, _ = A.post(f"https://api.parallel.ai/v1beta/findall/runs/{fid}/enrich", par_headers(), enrich_body, timeout=120)
    run, end = j, deadline()
    while fid and time.time() < end:
        time.sleep(CFG["poll_seconds"]["parallel"])
        s2, t2 = get(f"https://api.parallel.ai/v1beta/findall/runs/{fid}", par_headers())
        if s2 == 200:
            run = A.parse(t2)
            if not (run.get("status") or {}).get("is_active", True):
                break
    if fid and (run.get("status") or {}).get("is_active", True):
        A.post(f"https://api.parallel.ai/v1beta/findall/runs/{fid}/cancel", par_headers(), {}, timeout=60)  # stop per-match billing
        time.sleep(20)
    result = {}
    if fid:
        s3, t3 = get(f"https://api.parallel.ai/v1beta/findall/runs/{fid}/result", par_headers(), timeout=120)
        result = A.parse(t3) if s3 == 200 else {"_status": s3, "_body": t3[:500]}
    cands = result.get("candidates") or []
    matched = [c for c in cands if c.get("match_status") == "matched"]
    usd = (pc["findall_fixed_usd"] + len(matched) * pc["findall_per_match_usd"] + len(matched) * pc["enrich_per_match_usd"]) if fid else 0.0
    spend("parallel", usd=usd, release=held)
    rec = {"set": "S5", "id": spec["id"], "provider": "parallel", "ts": now(), "status": st, "run_id": fid,
           "enrich_status": est, "terminal_status": (run.get("status") or {}).get("status"),
           "stop_reason": (run.get("status") or {}).get("termination_reason"), "latency_ms": int((time.time() - t0) * 1000),
           "cost": {"usd": usd, "basis": "list-price formula: fixed + per match + enrichment per match",
                    "matched": len(matched), "candidates": len(cands)},
           "request": body, "enrich_request": enrich_body, "raw": {"run": run, "result": result}}
    log("S5", "parallel", rec)
    return rec


def par_s6(s6, row):
    pc = CFG["parallel"]
    sch = row_schema(s6, strict=True)
    sch["description"] = s6["instructions"]
    body = {"input": {"company": row["name"], "homepage": row["homepage"]}, "processor": pc["task_processor"],
            "task_spec": {"input_schema": {"type": "json", "json_schema": {"type": "object", "properties": {
                "company": {"type": "string"}, "homepage": {"type": "string"}}, "required": ["company", "homepage"],
                "additionalProperties": False}},
                "output_schema": {"type": "json", "json_schema": sch}}}
    held = can_spend("parallel", worst_usd=pc["task_price_usd"])
    if held is None:
        rec = {"set": "S6", "id": row["id"], "provider": "parallel", "ts": now(), "status": "skipped_cap", "request": body}
        log("S6", "parallel", rec)
        return rec
    t0 = time.time()
    st, txt, _ = A.post("https://api.parallel.ai/v1/tasks/runs", par_headers(), body, timeout=120, pace=A.PACE["parallel"])
    j = A.parse(txt)
    rid = j.get("run_id") if st in (200, 201, 202) else None
    run, end = j, deadline()
    while rid and time.time() < end:
        time.sleep(CFG["poll_seconds"]["parallel"])
        s2, t2 = get(f"https://api.parallel.ai/v1/tasks/runs/{rid}", par_headers())
        if s2 == 200:
            run = A.parse(t2)
            if run.get("status") in ("completed", "failed", "cancelled"):
                break
    result = {}
    if rid:
        s3, t3 = get(f"https://api.parallel.ai/v1/tasks/runs/{rid}/result", par_headers(), timeout=120)
        result = A.parse(t3) if s3 == 200 else {"_status": s3, "_body": t3[:500]}
    ok = run.get("status") == "completed"
    usd = pc["task_price_usd"] if ok else 0.0
    spend("parallel", usd=usd, release=held)
    out = result.get("output") or {}
    rec = {"set": "S6", "id": row["id"], "provider": "parallel", "ts": now(), "status": st, "run_id": rid,
           "terminal_status": run.get("status"), "latency_ms": int((time.time() - t0) * 1000),
           "cost": {"usd": usd, "basis": "list price per successful run"}, "request": body,
           "structured": out.get("content"), "grounding": out.get("basis"), "raw": result}
    log("S6", "parallel", rec)
    return rec


# ---------- Firecrawl ----------
def fc_headers():
    return {"Authorization": "Bearer " + os.environ["FIRECRAWL_API_KEY"]}


def fc_remaining():
    st, txt = get("https://api.firecrawl.dev/v2/team/credit-usage", fc_headers())
    return (A.parse(txt).get("data") or {}).get("remainingCredits") if st == 200 else None


def fc_agent(prompt, schema, set_name, item_id, max_credits):
    body = {"prompt": prompt, "schema": schema, "maxCredits": max_credits}
    led = ledger_view()["spent"]["firecrawl"]
    # The first runs of the day are free. Past them (or once a run was billed), usage spills over to the owner's paid
    # plan, so the run reserves its worst case (maxCredits at list value) and waits for room under the caps.
    paid = led["runs"] >= CFG["firecrawl"]["free_runs_per_day"] or led["credits"] > 0
    held = 0.0
    if paid:
        held = can_spend("firecrawl", worst_credits=max_credits, wait=True)
        if held is None:
            rec = {"set": set_name, "id": item_id, "provider": "firecrawl", "ts": now(), "status": "skipped_cap", "request": body}
            log(set_name, "firecrawl", rec)
            return rec
    before = fc_remaining()
    t0 = time.time()
    st, txt, _ = A.post("https://api.firecrawl.dev/v2/agent", fc_headers(), body, timeout=120, pace=A.PACE["firecrawl"])
    j = A.parse(txt)
    jid = j.get("id") if st == 200 else None
    run, end = j, deadline()
    while jid and time.time() < end:
        time.sleep(CFG["poll_seconds"]["firecrawl"])
        s2, t2 = get(f"https://api.firecrawl.dev/v2/agent/{jid}", fc_headers())
        if s2 == 200:
            run = A.parse(t2)
            if run.get("status") != "processing":
                break
    after = fc_remaining()
    delta = (before - after) if (before is not None and after is not None) else None
    reported = run.get("creditsUsed")
    billed = (reported or 0) if paid else max(delta or 0, 0)
    spend("firecrawl", credits=billed, release=held)
    rec = {"set": set_name, "id": item_id, "provider": "firecrawl", "ts": now(), "status": st, "run_id": jid,
           "terminal_status": run.get("status"), "latency_ms": int((time.time() - t0) * 1000),
           "cost": {"credits_used_reported": reported, "credits_billed": billed, "paid_run": paid,
                    "remaining_before": before, "remaining_after": after, "note": "first 5 agent runs a day are free"},
           "request": body, "structured": run.get("data"), "raw": run}
    log(set_name, "firecrawl", rec)
    return rec


def fc_s5(spec, excl, n):
    return fc_agent(list_prompt(spec, n, exclusions=excl), list_schema(spec, n), "S5", spec["id"], CFG["firecrawl"]["s5_max_credits"])


def fc_s6(s6, rows, batch_id):
    names = "\n".join(f"{i + 1}. {r['name']} ({r['homepage']})" for i, r in enumerate(rows))
    item = row_schema(s6)
    schema = {"type": "object", "properties": {"companies": {"type": "array", "items": item}}, "required": ["companies"]}
    prompt = (f"For each of these {len(rows)} companies, research current public sources and fill every field of the schema, "
              f"one entry per company, using the company name exactly as given.\n{names}\n\n{s6['instructions']}\n\nField definitions:\n"
              + "\n".join(f"- {k}: {v}" for k, v in s6["fields"].items()))
    return fc_agent(prompt, schema, "S6", batch_id, CFG["firecrawl"]["s6_max_credits"])


# ---------- runner ----------
def run_threads(jobs):
    ts = [threading.Thread(target=f, args=a) for f, a in jobs]
    for t in ts:
        t.start()
        time.sleep(1.0)
    for t in ts:
        t.join()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, help="S5, S6, SP, or a comma list such as S5,S6")
    ap.add_argument("--providers", default="exa,parallel,firecrawl")
    ap.add_argument("--only", default="")
    ap.add_argument("--pairs", default="", help="optional set:provider pairs to run, e.g. S5:exa,S6:firecrawl")
    a = ap.parse_args()
    sets = a.set.split(",")
    provs = a.providers.split(",")
    pairs = set(filter(None, a.pairs.split(",")))
    ok = lambda s_, p_: p_ in provs and (not pairs or f"{s_}:{p_}" in pairs)
    only = set(filter(None, a.only.split(",")))
    clean = lambda ex: [{"name": e["name"], "url": e["url"]} for e in ex]
    n = CFG["max_items"]
    jobs, fc_chain = [], []
    if "S5" in sets:
        spec = json.loads((ROOT / "sets" / "S5.json").read_text())
        for l in [l for l in spec["lists"] if not only or l["id"] in only]:
            if ok("S5", "exa"):
                jobs.append((exa_s5, (l, clean(l["exclusions"]), n)))
            if ok("S5", "parallel"):
                jobs.append((par_s5, (l, clean(l["exclusions"]), n)))
            if ok("S5", "firecrawl"):
                fc_chain.append((fc_s5, (l, clean(l["exclusions"]), n)))
    if "S6" in sets:
        s6 = json.loads((ROOT / "sets" / "S6.json").read_text())
        rows = [r for r in s6["companies"] if not only or r["id"] in only]
        for r in rows:
            if ok("S6", "exa"):
                jobs.append((exa_s6, (s6, r)))
            if ok("S6", "parallel"):
                jobs.append((par_s6, (s6, r)))
        if ok("S6", "firecrawl"):
            k = CFG["firecrawl"]["rows_per_run"]
            for i in range(0, len(rows), k):
                fc_chain.append((fc_s6, (s6, rows[i:i + k], f"b{i // k + 1:02d}")))
    # Firecrawl's /agent runs go one at a time (the free plan allows 2 /agent requests a minute).
    jobs.append((lambda chain: [f(*args) for f, args in chain], (fc_chain,)))
    run_threads(jobs)
    if "SP" in sets and ok("SP", "exa"):
        sp = json.loads((ROOT / "sets" / "SP.json").read_text())
        for it in sp["urls"]:
            if not only or it["id"] in only:
                exa_similar(it, sp["num_results"])
    d = ledger_view()
    print("LEDGER_B", json.dumps(d), "| total spent incl. Stage A (USD, credits at list):", round(spent_total_usd(d), 2))


if __name__ == "__main__":
    main()
