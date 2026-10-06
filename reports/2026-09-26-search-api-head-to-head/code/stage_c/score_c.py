#!/usr/bin/env python3
"""Stage C: score the blind verdicts by provider-arm, as judged (Amendment 8's metrics) and corrected (Amendment 12).

Writes to data/stage-c/:
- claims.json: every judged claim (the judge's wording, URL, verdict, tier, usefulness, provider-arms), as judged;
- cid_verdicts.json: every research item's verdict and provider-arm;
- scores.json: Amendment 8's metrics as judged (Amendment 12 doesn't change them);
- scores_corrected.json: the corrected primary and its sensitivities, from corrections.json (Amendment 12, post hoc);
- runs_meta.json (local mode only): status, time, latency, cost and counts per run, with no vendor text.
In local mode it also writes the sanitized grading record: grading/judged_J*.json (ids, verdicts, tiers, the judges'
claims and notes, quotes over 15 words cut), grading/_attrib/*.json (item ids to provider-arms) and
grading/assignment.json (which judge graded which question).

Social-media profile and post URLs are hashed with export_data.py's rule, and the script refuses to write one that
isn't hashed.

Usage:
  python3 score_c.py                       # local run folder: grading/C/, runs/ (vendor output; not committed)
  python3 score_c.py --data ../../data/stage-c   # rebuild every output from the committed record
"""
import argparse, json, math, pathlib, random, re, statistics, sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from export_data import is_unhashed_social, redact_text, redact_url  # noqa: E402  (the shared social-URL rule)

PROVIDERS = ["exa", "tavily", "parallel", "firecrawl"]
ARMS = [f"{p}:{a}" for p in PROVIDERS for a in ("search", "research")]
CREDIT_USD = {"tavily": 0.008, "firecrawl": 0.005}  # list value, as the cap guard used; scores.json is as judged
# Credit prices across plans (the KB's own price check, _scratch/review/market-sizing-red-team/code/rt1/verify-V5-prices.md
# rows 20 and 34): Tavily from its Growth plan ($0.005) to pay-as-you-go ($0.008); Firecrawl from Scale ($749 for 1M,
# $0.00075) to the Hobby top-up ($0.005), as Stage B's range (Amendment 7).
CREDIT_RANGE = {"tavily": (0.005, 0.008), "firecrawl": (0.00075, 0.005)}
COST_BASIS = {"exa": "billed (the API's costDollars)", "parallel": "list-price estimate ($0.005 a search call, $0.10 a Task run)",
              "tavily": "credits", "firecrawl": "credits"}
PDT = timezone(timedelta(hours=-7))  # the search harness logs local time; the push log fixes it as PDT (Amendment 12)
PATH_RE = re.compile(r"[\w./-]+\.(?:md|json|py|csv|txt)")
QUOTE_RE = re.compile(r'"([^"]+)"|“([^”]+)”')


def load_json(p):
    return json.loads(pathlib.Path(p).read_text())


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=1, ensure_ascii=False)
    for s in walk_strings(data):
        if is_unhashed_social(s):
            raise SystemExit(f"refusing to write {path.name}: unhashed social URL in {s[:80]!r}")
    path.write_text(text)


def walk_strings(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield from walk_strings(k)
            yield from walk_strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from walk_strings(v)
    elif isinstance(x, str):
        yield x


def cid(c):
    return f"{c['question']}#{c['n']}"


# ---------- the grading record ----------
def cut_quotes(text, limit=15):
    """Cut any quoted span over `limit` words to its first `limit` words (the judges' 15-word rule, enforced)."""
    def cut(m):
        q = m.group(1) if m.group(1) is not None else m.group(2)
        w = q.split()
        if len(w) <= limit:
            return m.group(0)
        return m.group(0)[0] + " ".join(w[:limit]) + " …" + m.group(0)[-1]
    return QUOTE_RE.sub(cut, text) if isinstance(text, str) else text


def sanitize_judged(j):
    keep_claim = ["claim", "tier", "verdict", "usefulness", "verified_url", "date", "source_type", "kb_file",
                  "support_full", "support_partial"]
    out = {"judge": j.get("judge"), "questions": []}
    for q in j["questions"]:
        claims = []
        for c in q.get("claims") or []:
            d = {k: c.get(k) for k in keep_claim}
            d["claim"] = cut_quotes(redact_text(d["claim"]))
            d["verified_url"] = redact_text(d["verified_url"]) if d["verified_url"] else d["verified_url"]
            claims.append(d)
        out["questions"].append({"id": q["id"], "status": q.get("status"), "claims": claims,
                                 "cid_verdicts": q.get("cid_verdicts") or {}, "notes": cut_quotes(redact_text(q.get("notes") or ""))})
    return out


def grading_record(args):
    """Return (judged files, attribution maps, assignment), sanitized; in local mode also write them to the export."""
    if args.data:
        g = pathlib.Path(args.data) / "grading"
        judged = {p.stem: load_json(p) for p in sorted(g.glob("judged_J*.json"))}
        attrib = {p.stem: load_json(p) for p in sorted((g / "_attrib").glob("*.json"))}
        return judged, attrib, load_json(g / "assignment.json")
    g = ROOT / "grading" / "C"
    judged = {p.stem: sanitize_judged(load_json(p)) for p in sorted(g.glob("judged_J*.json"))}
    attrib = {p.stem: load_json(p) for p in sorted((g / "_attrib").glob("*.json"))}
    summary = attrib.get("summary", {})
    assignment = {"note": "Each judge graded three questions, balanced by pool size (Amendment 11). Pool sizes: result URLs and research items.",
                  "judges": {j["judge"]: [q["id"] for q in j["questions"]] for j in judged.values()},
                  "pools": {q: {"results": s["results"], "research_items": s["research_items"]} for q, s in summary.items()}}
    out = OUTD / "grading"
    for name, j in judged.items():
        dump(out / f"{name}.json", j)
    for name, a in attrib.items():
        dump(out / "_attrib" / f"{name}.json", a)
    dump(out / "assignment.json", assignment)
    return judged, attrib, assignment


def build_claims(judged, attrib):
    claims, cidv = [], []
    for name in sorted(judged):
        for q in judged[name]["questions"]:
            qid = q["id"]
            amap = attrib[qid]["attrib"]
            for n, c in enumerate(q.get("claims") or []):
                full = sorted({arm for iid in (c.get("support_full") or []) for arm in amap.get(iid, [])})
                part = sorted({arm for iid in (c.get("support_partial") or []) for arm in amap.get(iid, [])} - set(full))
                claims.append({"question": qid, "n": n + 1, "claim": c.get("claim"), "tier": c.get("tier"), "verdict": c.get("verdict"),
                               "usefulness": c.get("usefulness"), "verified_url": c.get("verified_url"), "date": c.get("date"),
                               "source_type": c.get("source_type"), "kb_file": c.get("kb_file"), "arms_full": full, "arms_partial": part,
                               "status_of_question": q.get("status")})
            for iid, v in (q.get("cid_verdicts") or {}).items():
                arms = amap.get(iid, [])
                cidv.append({"question": qid, "cid": iid, "verdict": v, "arm": arms[0] if arms else None})
    return claims, cidv


# ---------- run metadata, costs and latency ----------
def ts_utc(ts, arm):
    if not ts:
        return None
    t = datetime.fromisoformat(ts)
    if t.tzinfo is None:  # the search harness's time.strftime: local clock (PDT)
        t = t.replace(tzinfo=PDT)
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_runs_meta(runs_dir):
    """Sanitized run metadata from the local runs/ folder (no vendor text)."""
    out = []
    for p in ["exa", "parallel", "tavily", "firecrawl"]:
        for arm, path in (("search", runs_dir / "SV" / f"{p}.jsonl"), ("research", runs_dir / "SV-research" / f"{p}.jsonl")):
            for l in open(path, encoding="utf-8"):
                r = json.loads(l)
                s = r.get("structured")
                if isinstance(s, str):
                    try:
                        s = json.loads(s)
                    except Exception:
                        s = None
                out.append({"provider": p, "arm": arm, "question": r["id"], "status": r.get("status"),
                            "terminal_status": r.get("terminal_status"), "ts": r.get("ts"), "ts_utc": ts_utc(r.get("ts"), arm),
                            "latency_ms": r.get("latency_ms"),
                            "cost": {k: v for k, v in (r.get("cost") or {}).items() if k in ("usd", "credits", "credits_billed")},
                            "results": len(r.get("results") or []) if arm == "search" else None,
                            "figures": len((s or {}).get("figures") or []) if arm == "research" and isinstance(s, dict) else None,
                            "facts": len((s or {}).get("other_facts") or []) if arm == "research" and isinstance(s, dict) else None})
    return out


def runs_meta(args):
    if args.data:
        return load_json(pathlib.Path(args.data) / "runs_meta.json")
    meta = build_runs_meta(ROOT / "runs")
    dump(OUTD / "runs_meta.json", meta)
    return meta


def run_cost(m):
    c = m["cost"]
    return c.get("usd") or 0.0, c.get("credits", c.get("credits_billed")) or 0


def counted(m, dropped):
    return (m["provider"], m["arm"], m["question"]) not in dropped


def arm_costs(meta, dropped=frozenset()):
    """Cost per arm: (usd at list value, credits, usd low, usd high), over the runs a variant counts."""
    acc = defaultdict(lambda: [0.0, 0])
    for m in meta:
        if counted(m, dropped):
            usd, cr = run_cost(m)
            a = acc[f"{m['provider']}:{m['arm']}"]
            a[0] += usd
            a[1] += cr
    out = {}
    for arm in ARMS:
        p = arm.split(":")[0]
        usd, cr = acc[arm]
        lo, hi = CREDIT_RANGE.get(p, (0.0, 0.0))
        out[arm] = {"usd_list": usd + cr * CREDIT_USD.get(p, 0.0), "credits": cr, "usd_low": usd + cr * lo, "usd_high": usd + cr * hi}
    return out


def latencies(meta, dropped=frozenset()):
    lat = defaultdict(list)
    for m in meta:
        if m.get("latency_ms") and counted(m, dropped) and ((m["arm"] == "search" and m["status"] == 200) or m.get("terminal_status") == "completed"):
            lat[f"{m['provider']}:{m['arm']}"].append(m["latency_ms"] / 1000)
    return {k: round(statistics.median(v), 1) for k, v in lat.items() if v}


# ---------- Amendment 8's metrics, as judged ----------
def scores_as_judged(claims, cidv, meta):
    cost = {a: v["usd_list"] for a, v in arm_costs(meta).items()}
    lat = latencies(meta)
    per = {}
    for arm in ARMS:
        mine = [c for c in claims if arm in c["arms_full"]]
        new = [c for c in mine if c["verdict"] == "new"]
        new1 = [c for c in new if c["tier"] == 1]
        uniq1 = [c for c in new1 if c["arms_full"] == [arm]]
        uniq = [c for c in new if c["arms_full"] == [arm]]
        qs = sorted({c["question"] for c in new})
        mine_cid = [v for v in cidv if v["arm"] == arm]
        checkable = [v for v in mine_cid if v["verdict"] in ("supported", "unsupported", "false")]
        bad = [v for v in checkable if v["verdict"] in ("unsupported", "false")]
        per[arm] = {"new_tier1": len(new1), "unique_new_tier1": len(uniq1), "new_all": len(new), "unique_new_all": len(uniq),
                    "known": sum(1 for c in mine if c["verdict"] == "known"), "false": sum(1 for c in mine if c["verdict"] == "false"),
                    "questions_with_new": len(qs), "usefulness_sum_new": sum((c.get("usefulness") or 0) for c in new),
                    "research_items": len(mine_cid), "research_checkable": len(checkable), "research_errors": len(bad),
                    "research_error_rate": round(len(bad) / len(checkable), 3) if checkable else None,
                    "cost_usd": round(cost.get(arm, 0.0), 3),
                    "cost_per_new": round(cost[arm] / len(new), 3) if new else None,
                    "cost_per_new_tier1": round(cost[arm] / len(new1), 3) if new1 else None,
                    "median_latency_s": lat.get(arm)}
    by_provider = {}
    for p in PROVIDERS:
        arms = {f"{p}:search", f"{p}:research"}
        new = [c for c in claims if c["verdict"] == "new" and arms & set(c["arms_full"])]
        new1 = [c for c in new if c["tier"] == 1]
        others = set(ARMS) - arms
        uniq1 = [c for c in new1 if not (set(c["arms_full"]) & others)]
        by_provider[p] = {"new_tier1": len(new1), "unique_new_tier1": len(uniq1), "new_all": len(new),
                          "cost_usd": round(cost[f"{p}:search"] + cost[f"{p}:research"], 3)}
    pool_new1 = [c for c in claims if c["verdict"] == "new" and c["tier"] == 1]
    status = defaultdict(set)
    for c in claims:
        status[c["question"]].add(c.get("status_of_question"))
    return {"arms": per, "providers": by_provider,
            "totals": {"claims": len(claims), "new": sum(1 for c in claims if c["verdict"] == "new"), "new_tier1": len(pool_new1),
                       "known": sum(1 for c in claims if c["verdict"] == "known"), "false": sum(1 for c in claims if c["verdict"] == "false"),
                       "questions_answered": sorted(q for q, s in status.items() if "answered" in s)}}


# ---------- Amendment 12: the corrected scoring ----------
VARIANTS = {
    "as_judged": {"label": "As judged", "novelty": False, "stage_b": False, "tier_o5": False, "tier_g3m3": False, "dedupe": False, "tavily_prereg": False},
    "primary": {"label": "Corrected primary", "novelty": True, "stage_b": False, "tier_o5": True, "tier_g3m3": True, "dedupe": True, "tavily_prereg": True},
    "stage_b_novelty": {"label": "Sensitivity: Stage B's novelty rule (`_scratch/` isn't the KB)", "novelty": True, "stage_b": True, "tier_o5": True,
                        "tier_g3m3": True, "dedupe": True, "tavily_prereg": True},
    "g3m3_counted": {"label": "Sensitivity: g3 and m3 counted as tier 1", "novelty": True, "stage_b": False, "tier_o5": True, "tier_g3m3": False,
                     "dedupe": True, "tavily_prereg": True},
    "amendment10": {"label": "Sensitivity: Amendment 10's scoring (Tavily's four re-runs count)", "novelty": True, "stage_b": False, "tier_o5": True,
                    "tier_g3m3": True, "dedupe": True, "tavily_prereg": False},
    "g3m3_amendment10": {"label": "Both: g3 and m3 counted, and Amendment 10's scoring", "novelty": True, "stage_b": False, "tier_o5": True,
                         "tier_g3m3": False, "dedupe": True, "tavily_prereg": False},
}


def scratch_only(kb):
    paths = PATH_RE.findall(kb or "")
    return bool(paths) and all(p.startswith("_scratch/") for p in paths)


def check_corrections(claims, corr):
    ids = {cid(c) for c in claims}
    by = {cid(c): c for c in claims}
    for e in corr["claims"]:
        if e["claim"] not in ids:
            raise SystemExit(f"corrections.json names an unknown claim {e['claim']}")
        c = by[e["claim"]]
        if e["change"] in ("verdict", "tier") and c[e["change"]] != e["from"]:
            raise SystemExit(f"corrections.json: {e['claim']} {e['change']} is {c[e['change']]}, not {e['from']}")
    rule = corr["rules"]["tavily_prereg330"]
    arm, qs = rule["arm"], set(rule["questions"])
    want = sorted(cid(c) for c in claims if c["question"] in qs and (arm in c["arms_full"] or arm in c["arms_partial"]))
    listed = sorted(e["claim"] for e in corr["claims"] if e["change"] == "arms")
    if want != listed:
        raise SystemExit(f"corrections.json: the prereg:330 arm entries don't match the rule ({len(listed)} listed, {len(want)} touched)")


def apply_variant(claims, corr, v):
    C = {cid(c): dict(c, arms_full=list(c["arms_full"]), arms_partial=list(c["arms_partial"])) for c in claims}
    for e in corr["claims"]:
        c = C[e["claim"]]
        if e["change"] == "verdict" and v["novelty"]:
            c["verdict"], c["kb_file"] = e["to"], e["kb_file"]
        elif e["change"] == "tier" and ((e["group"] == "o5" and v["tier_o5"]) or (e["group"] == "g3m3" and v["tier_g3m3"])):
            c["tier"] = e["to"]
        elif e["change"] == "arms" and v["tavily_prereg"]:
            c["arms_full"] = [a for a in c["arms_full"] if a != e["remove"]]
            c["arms_partial"] = [a for a in c["arms_partial"] if a != e["remove"]]
    if v["stage_b"]:
        for c in C.values():
            if c["verdict"] == "known" and scratch_only(c["kb_file"]):
                c["verdict"], c["stage_b_flip"] = "new", True
    return C


def units(C, corr, v):
    """Counting units: fact groups (claims that state the same thing), bundles, and single claims."""
    groups, member_of, bundles = {}, {}, {}
    if v["dedupe"]:
        for e in corr["claims"]:
            if e["change"] == "fact_id":
                g = groups.setdefault(e["fact_id"], {"members": [], "canonical": None})
                g["members"].append(e["claim"])
                member_of[e["claim"]] = e["fact_id"]
                if e.get("canonical"):
                    g["canonical"] = e["claim"]
            elif e["change"] == "bundle":
                bundles[e["claim"]] = e["restates"]
    U = {}
    for fid, g in groups.items():
        ms = [C[m] for m in g["members"]]
        can = C[g["canonical"]]
        verdicts = {m["verdict"] for m in ms}
        verdict = "known" if "known" in verdicts else ("false" if verdicts == {"false"} else "new")
        U[fid] = {"id": fid, "claims": g["members"], "question": can["question"], "tier": can["tier"], "verdict": verdict,
                  "usefulness": max((m.get("usefulness") or 0) for m in ms),
                  "arms": set().union(*[set(m["arms_full"]) for m in ms])}
    for k, c in C.items():
        if k in member_of or k in bundles:
            continue
        U[k] = {"id": k, "claims": [k], "question": c["question"], "tier": c["tier"], "verdict": c["verdict"],
                "usefulness": c.get("usefulness") or 0, "arms": set(c["arms_full"])}
    unit_of = {k: member_of.get(k, k) for k in C}
    B = {}
    for k, restates in bundles.items():
        c = C[k]
        facts = [unit_of[r] for r in restates]
        B[k] = {"id": k, "claims": [k], "question": c["question"], "tier": c["tier"], "verdict": c["verdict"],
                "usefulness": c.get("usefulness") or 0, "arms": set(c["arms_full"]), "restates": facts}
    return U, B


def count_variant(claims, cidv, meta, corr, key):
    v = VARIANTS[key]
    C = apply_variant(claims, corr, v)
    U, B = units(C, corr, v)
    # who else stated each fact (for "found by no other arm/provider"): a bundle's arms count for the facts it restates
    also = defaultdict(set)
    for b in B.values():
        for f in b["restates"]:
            also[f] |= b["arms"]
    dropped_runs = set()
    if v["tavily_prereg"]:
        rule = corr["rules"]["tavily_prereg330"]
        prov, arm = rule["arm"].split(":")
        dropped_runs = {(prov, arm, q) for q in rule["questions"]}
    cost = arm_costs(meta, dropped_runs)
    lat = latencies(meta, dropped_runs)
    bad_cids = set()
    if v["tavily_prereg"]:
        rule = corr["rules"]["tavily_prereg330"]
        bad_cids = {(x["question"], x["cid"]) for x in cidv if x["arm"] == rule["arm"] and x["question"] in rule["questions"]}

    def tally(arm_set):
        """Counted units for a set of provider-arms (one arm, or a provider's two)."""
        got = []
        for u in U.values():
            if u["arms"] & arm_set:
                got.append((u, u["arms"] | also[u["id"]]))
        for b in B.values():
            if b["arms"] & arm_set and not all(U[f]["arms"] & arm_set for f in b["restates"]):
                got.append((b, b["arms"] | set().union(*[U[f]["arms"] for f in b["restates"]])))
        return got

    per, perq = {}, {}
    for arm in ARMS:
        got = tally({arm})
        new = [(u, w) for u, w in got if u["verdict"] == "new"]
        new1 = [(u, w) for u, w in new if u["tier"] == 1]
        qs = sorted({c["question"] for c in C.values() if c["verdict"] == "new" and arm in c["arms_full"]})
        mine_cid = [x for x in cidv if x["arm"] == arm and (x["question"], x["cid"]) not in bad_cids]
        checkable = [x for x in mine_cid if x["verdict"] in ("supported", "unsupported", "false")]
        errs = [x for x in checkable if x["verdict"] in ("unsupported", "false")]
        cst = cost[arm]
        per[arm] = {"new_tier1": len(new1), "unique_new_tier1": sum(1 for u, w in new1 if w == {arm}),
                    "new_all": len(new), "unique_new_all": sum(1 for u, w in new if w == {arm}),
                    "useful_new_tier1": sum(1 for u, w in new1 if u["usefulness"] >= 2),
                    "questions_with_new": len(qs), "research_items": len(mine_cid), "research_checkable": len(checkable),
                    "research_errors": len(errs), "research_error_rate": round(len(errs) / len(checkable), 3) if checkable else None,
                    "cost_usd_list": round(cst["usd_list"], 3), "credits": cst["credits"],
                    "cost_usd_low": round(cst["usd_low"], 4), "cost_usd_high": round(cst["usd_high"], 4),
                    "cost_per_new_low": round(cst["usd_low"] / len(new), 4) if new else None,
                    "cost_per_new_high": round(cst["usd_high"] / len(new), 4) if new else None,
                    "cost_per_new_tier1_low": round(cst["usd_low"] / len(new1), 4) if new1 else None,
                    "cost_per_new_tier1_high": round(cst["usd_high"] / len(new1), 4) if new1 else None,
                    "cost_per_new_tier1_list": round(cst["usd_list"] / len(new1), 4) if new1 else None,
                    "median_latency_s": lat.get(arm)}
        pq = Counter(u["question"] for u, w in new1)
        perq[arm] = {q["id"]: pq.get(q["id"], 0) for q in SET["items"]}
    prov = {}
    for p in PROVIDERS:
        mine = {f"{p}:search", f"{p}:research"}
        got = tally(mine)
        new = [(u, w) for u, w in got if u["verdict"] == "new"]
        new1 = [(u, w) for u, w in new if u["tier"] == 1]
        lo = cost[f"{p}:search"]["usd_low"] + cost[f"{p}:research"]["usd_low"]
        hi = cost[f"{p}:search"]["usd_high"] + cost[f"{p}:research"]["usd_high"]
        prov[p] = {"new_tier1": len(new1), "unique_new_tier1": sum(1 for u, w in new1 if w <= mine),
                   "new_all": len(new), "unique_new_all": sum(1 for u, w in new if w <= mine),
                   "cost_usd_low": round(lo, 4), "cost_usd_high": round(hi, 4),
                   "cost_per_new_tier1_low": round(lo / len(new1), 4) if new1 else None,
                   "cost_per_new_tier1_high": round(hi / len(new1), 4) if new1 else None}
    # distinct facts: bundles restate other claims, and a claim no counted run stated (Tavily's refused runs) drops out
    live = [u for u in U.values() if u["arms"] or u["verdict"] == "false"]
    totals = {"claims_judged": len(C), "distinct": len(live), "bundles": len(B),
              "no_counted_run": sum(1 for u in U.values() if not u["arms"] and u["verdict"] != "false"),
              "new": sum(1 for u in live if u["verdict"] == "new"),
              "new_tier1": sum(1 for u in live if u["verdict"] == "new" and u["tier"] == 1),
              "known": sum(1 for u in live if u["verdict"] == "known"),
              "false": sum(1 for u in live if u["verdict"] == "false"),
              "stage_b_flips": sum(1 for c in C.values() if c.get("stage_b_flip"))}
    byq = Counter(u["question"] for u in live if u["verdict"] == "new" and u["tier"] == 1)
    qarms = defaultdict(set)
    for u in live:
        if u["verdict"] == "new" and u["tier"] == 1:
            qarms[u["question"]] |= u["arms"]
    return {"label": v["label"], "switches": {k: x for k, x in v.items() if k != "label"}, "arms": per, "providers": prov,
            "totals": totals, "per_question_new_tier1": perq,
            "questions": {q["id"]: {"new_tier1": byq.get(q["id"], 0), "arms": sorted(qarms.get(q["id"], ()))} for q in SET["items"]}}


def sign_p(x, y):
    w = sum(1 for q in x if x[q] > y[q]); l = sum(1 for q in x if x[q] < y[q]); n = w + l
    if not n:
        return w, l, 1.0
    k = min(w, l)
    return w, l, min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def boot(x, y, B=20000, seed=7):
    ids = list(x)
    r = random.Random(seed); d = []
    for _ in range(B):
        s = [r.choice(ids) for _ in ids]
        d.append(sum(x[q] - y[q] for q in s))
    d.sort()
    return d[int(0.025 * B)], d[int(0.975 * B)]


def holm(ps):
    """Holm-Bonferroni adjusted p-values, in the input order."""
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj, run = [0.0] * len(ps), 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (len(ps) - rank) * ps[i]))
        adj[i] = run
    return adj


def tests(variant):
    """Post hoc (Amendment 12): the leader in each arm against each other provider, sign test and bootstrap, Holm-adjusted."""
    A, rows = variant["arms"], []
    for a in ("search", "research"):
        # prereg:351-352: most new tier-1 claims, cost per claim breaking ties (credits at list value, as the prereg values them)
        ranked = sorted(["exa", "parallel", "tavily", "firecrawl"],
                        key=lambda p: (-A[f"{p}:{a}"]["new_tier1"], A[f"{p}:{a}"]["cost_per_new_tier1_list"] or 1e9))
        top = ranked[0]
        for o in ranked[1:]:
            x, y = variant["per_question_new_tier1"][f"{top}:{a}"], variant["per_question_new_tier1"][f"{o}:{a}"]
            w, l, pv = sign_p(x, y)
            lo, hi = boot(x, y)
            rows.append({"arm": a, "leader": top, "other": o, "leader_n": sum(x.values()), "other_n": sum(y.values()),
                         "ahead": w, "behind": l, "p": round(pv, 4), "boot_lo": lo, "boot_hi": hi})
    for r, pa in zip(rows, holm([r["p"] for r in rows])):
        r["p_holm"] = round(pa, 4)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="rebuild from the committed record in this folder (for example ../../data/stage-c)")
    args = ap.parse_args()
    global OUTD, SET
    OUTD = pathlib.Path(args.data).resolve() if args.data else ROOT / "data" / "stage-c"
    SET = load_json(ROOT / "sets" / "SV.json")
    judged, attrib, _ = grading_record(args)
    claims, cidv = build_claims(judged, attrib)
    meta = runs_meta(args)
    corr = load_json(OUTD / "corrections.json")
    check_corrections(claims, corr)
    scores = scores_as_judged(claims, cidv, meta)
    variants = {k: count_variant(claims, cidv, meta, corr, k) for k in VARIANTS}
    for k in ("new_tier1", "unique_new_tier1", "new_all", "questions_with_new"):  # the correction code reproduces the as-judged scores
        for arm in ARMS:
            if k in scores["arms"][arm] and variants["as_judged"]["arms"][arm][k] != scores["arms"][arm][k]:
                raise SystemExit(f"as-judged mismatch: {arm} {k}")
    judged_tests = tests(variants["as_judged"])
    corrected = {"about": "Amendment 12 (post hoc). The corrected primary and its sensitivities, from corrections.json; scores.json keeps the as-judged figures.",
                 "cost_basis": {"credit_range_usd": CREDIT_RANGE, "list_value_usd": CREDIT_USD, "by_provider": COST_BASIS},
                 "variants": variants, "tests": {"primary": tests(variants["primary"]), "as_judged": judged_tests}}
    dump(OUTD / "claims.json", claims)
    dump(OUTD / "cid_verdicts.json", cidv)
    dump(OUTD / "scores.json", scores)
    dump(OUTD / "scores_corrected.json", corrected)
    P = variants["primary"]
    print(json.dumps({a: [P["arms"][a][k] for k in ("new_tier1", "unique_new_tier1", "new_all")] for a in ARMS}))
    print(json.dumps({p: [P["providers"][p][k] for k in ("new_tier1", "unique_new_tier1", "new_all")] for p in PROVIDERS}))


if __name__ == "__main__":
    main()
