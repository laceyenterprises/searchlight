#!/usr/bin/env python3
"""Stage B grading prep (Amendment 5): blind pools for S6 (schema fill) and S5 (entity lists), and the SP domain list.

S6: one item per company × field. Each provider's value and cited URL becomes an anonymous value (v1, v2, …),
shuffled with a recorded seed. The KB ground truth from sets/S6_truth.json rides along for the judge. Attribution
(value id → provider) goes to grading/b/_attrib/.
S5: one pool per list. Entities from all providers are deduplicated by registrable domain (or name when there's no
URL). Each keeps its distinct names, claimed field values, and evidence URLs, with no provider names. Entities that
match an exclusion-list entry are flagged `matches_exclusion`.

Usage: python3 grade_prep_b.py S6 | S5 [--v2]
  The default rebuilds exactly the pools the Stage B judges saw. --v2 (Amendment 7, for future runs) keys exclusions
  only on non-shared hosts, also matches exclusions by name containment, and falls back to an entity's evidence-URL
  domain when it has no URL, so null-URL duplicates dedupe (the Coherence miss).
"""
import collections, json, pathlib, random, re, sys
from urllib.parse import urlsplit

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "grading" / "b"
(OUT / "_attrib").mkdir(parents=True, exist_ok=True)
P = ["exa", "parallel", "firecrawl"]


def last_records(set_name):
    """Last non-skipped record per (provider, id)."""
    out = {}
    for p in P:
        f = ROOT / "runs" / set_name / f"{p}.jsonl"
        if f.exists():
            for line in open(f):
                r = json.loads(line)
                if r.get("status") == "skipped_cap":
                    continue
                out[(p, r["id"])] = r
    return out


def reg(u):
    try:
        h = (urlsplit(u if "://" in u else "https://" + u).hostname or "").lower()
    except Exception:
        return ""
    h = h[4:] if h.startswith("www.") else h
    parts = h.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else h


V2 = "--v2" in sys.argv
SHARED_HOSTS = {"github.com", "ycombinator.com", "producthunt.com", "exa.ai", "algolia.com", "medium.com", "substack.com",
                "notion.site", "linkedin.com", "x.com", "twitter.com", "youtube.com"}
GENERIC = {"inc", "llc", "ltd", "corp", "corporation", "company", "co", "the", "labs", "ai", "technologies", "gmbh", "plc"}


def lead(name):
    """First meaningful word of a name, before any parenthesis, comma, 'by', or dash (v2 name matching)."""
    base = re.split(r"\(|,| by | - | – ", (name or "").lower())[0]
    words = [w for w in re.findall(r"[a-z0-9]+", base) if w not in GENERIC]
    return words[0] if words and len(words[0]) >= 4 else None


def nname(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower().replace("(search api)", "").replace("inc", ""))


def s6():
    s6 = json.loads((ROOT / "sets" / "S6.json").read_text())
    truth = {c["id"]: c for c in json.loads((ROOT / "sets" / "S6_truth.json").read_text())["companies"]}
    recs = last_records("S6")
    fc_rows = {}
    for (p, rid), r in recs.items():  # Firecrawl batches: map rows back to companies by name
        if p != "firecrawl":
            continue
        rows = ((r.get("structured") or {}).get("companies") or []) if isinstance(r.get("structured"), dict) else []
        for row in rows:
            fc_rows[nname(row.get("company"))] = row
    items, attrib, completeness = [], {}, collections.Counter()
    for c in s6["companies"]:
        per = {}
        for p in ("exa", "parallel"):
            r = recs.get((p, c["id"]))
            per[p] = (r or {}).get("structured") if isinstance((r or {}).get("structured"), dict) else None
        per["firecrawl"] = fc_rows.get(nname(c["name"])) or next((v for k, v in fc_rows.items() if k and (k in nname(c["name"]) or nname(c["name"]) in k)), None)
        for f, definition in s6["fields"].items():
            vals = []
            for p in P:
                row = per.get(p) or {}
                v = row.get(f)
                if v in (None, "", [], {}):
                    continue
                completeness[p] += 1
                vals.append({"provider": p, "value": v if isinstance(v, str) else json.dumps(v), "source_url": row.get(f + "_source_url")})
            random.Random(f"20260926-S6-{c['id']}-{f}").shuffle(vals)
            cell = f"{c['id']}.{f}"
            pub = []
            for n, v in enumerate(vals, 1):
                vid = f"{cell}.v{n}"
                attrib[vid] = v["provider"]
                pub.append({"vid": vid, "value": v["value"], "source_url": v["source_url"]})
            gt = (truth.get(c["id"], {}).get("fields") or {}).get(f)
            items.append({"cell": cell, "company": c["name"], "homepage": c["homepage"], "field": f, "definition": definition,
                          "kb_ground_truth": gt, "values": pub})
    (OUT / "S6_pool.json").write_text(json.dumps(items, indent=1, ensure_ascii=False))
    (OUT / "_attrib" / "S6_attribution.json").write_text(json.dumps(attrib, indent=1))
    meta = {"cells": len(items), "values": len(attrib), "non_null_by_provider": dict(completeness),
            "cells_with_truth": sum(1 for i in items if i["kb_ground_truth"]),
            "firecrawl_rows_mapped": sum(1 for c in s6["companies"] if fc_rows.get(nname(c["name"])))}
    (OUT / "S6_meta.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta))


def entities_from(p, r, spec):
    if p in ("exa", "firecrawl"):
        st = r.get("structured") or {}
        return [e for e in (st.get("entities") or []) if isinstance(e, dict)]
    out = []  # Parallel FindAll: matched candidates, with enrichment and match-condition outputs
    res = (r.get("raw") or {}).get("result") or {}
    for c in res.get("candidates") or []:
        if c.get("match_status") != "matched":
            continue
        e = {"name": c.get("name"), "url": c.get("url")}
        for k, v in (c.get("output") or {}).items():
            e[k] = v.get("value") if isinstance(v, dict) else v
        e["_evidence"] = [ci.get("url") for b in (c.get("basis") or []) for ci in (b.get("citations") or []) if ci.get("url")]
        out.append(e)
    return out


def s5():
    spec = json.loads((ROOT / "sets" / "S5.json").read_text())
    recs = last_records("S5")
    meta = {}
    for l in spec["lists"]:
        excl_dom = {reg(e["url"]): e["name"] for e in l["exclusions"] if e.get("url") and not (V2 and reg(e["url"]) in SHARED_HOSTS)}
        excl_name = {nname(e["name"]): e["name"] for e in l["exclusions"]}
        pooled, attrib, returned = collections.OrderedDict(), collections.defaultdict(set), collections.Counter()
        for p in P:
            r = recs.get((p, l["id"]))
            if not r:
                continue
            for e in entities_from(p, r, l):
                returned[p] += 1
                ev = [u for u in (e.get("_evidence") or []) + [v for k, v in e.items() if k.endswith("evidence_url") and isinstance(v, str)] if u]
                key = reg(e.get("url") or "") or (V2 and ev and reg(ev[0])) or ("name:" + nname(e.get("name")))
                d = pooled.setdefault(key, {"names": [], "urls": [], "claimed": collections.defaultdict(list), "evidence_urls": []})
                if e.get("name") and e["name"] not in d["names"]:
                    d["names"].append(e["name"])
                if e.get("url") and e["url"] not in d["urls"]:
                    d["urls"].append(e["url"])
                for k, v in e.items():
                    if k in ("name", "url", "_evidence") or v in (None, "", []):
                        continue
                    vs = v if isinstance(v, str) else json.dumps(v)
                    if vs not in d["claimed"][k]:
                        d["claimed"][k].append(vs)
                    if k.endswith("evidence_url") and isinstance(v, str) and v not in d["evidence_urls"]:
                        d["evidence_urls"].append(v)
                for u in e.get("_evidence") or []:
                    if u not in d["evidence_urls"]:
                        d["evidence_urls"].append(u)
                attrib[key].add(p)
        keys = list(pooled)
        random.Random(f"20260926-S5-{l['id']}").shuffle(keys)
        items = []
        for n, k in enumerate(keys, 1):
            d = pooled[k]
            m = excl_dom.get(k) or next((excl_name[nname(x)] for x in d["names"] if nname(x) in excl_name), None)
            if V2 and not m:  # same lead word, for words of four or more characters
                excl_lead = {lead(e["name"]): e["name"] for e in l["exclusions"] if lead(e["name"])}
                m = next((excl_lead[lead(x)] for x in d["names"] if lead(x) in excl_lead), None)
            items.append({"eid": f"{l['id']}-e{n}", "key": k, "names": d["names"], "urls": d["urls"], "claimed": dict(d["claimed"]),
                          "evidence_urls": d["evidence_urls"][:6], "matches_exclusion": m})
        (OUT / f"S5_{l['id']}_pool.json").write_text(json.dumps({"list": {k: l[k] for k in ("id", "objective", "criteria", "fields")}, "entities": items}, indent=1, ensure_ascii=False))
        (OUT / "_attrib" / f"S5_{l['id']}_attribution.json").write_text(json.dumps({f"{l['id']}-e{n}": sorted(attrib[k]) for n, k in enumerate(keys, 1)}, indent=1))
        meta[l["id"]] = {"returned": dict(returned), "pooled": len(items), "matches_exclusion": sum(1 for i in items if i["matches_exclusion"])}
    (OUT / "S5_meta.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta, indent=1))


if __name__ == "__main__":
    {"S6": s6, "S5": s5}[sys.argv[1]]()
