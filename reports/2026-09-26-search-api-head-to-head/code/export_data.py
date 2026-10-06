#!/usr/bin/env python3
"""Export sanitized experiment data into the KB (allowlist, v2).

Vendor terms restrict storing API output, so only fields on the allowlist below are exported:
- run records: ids, status, latency, cost, our own request parameters, and per result its rank, URL, domain, published
  date, and text length;
- fetch records: status, latency, cost, content length, and crawl status codes;
- grading files: our own pools (URLs only), attribution maps, judge verdicts, and scores;
- Stage B (Amendment 5): entity names, URLs, cited URLs, costs, and our verdicts, through a per-file allowlist
  (ALLOW_B; Amendment 7). Provider-written values (S6 cell values, S5 claimed fields) are stripped from the pools; the
  verdicts on them stay.
Page titles, snippets, page text, and raw responses are never exported. Social-media profile and post URLs carry
people's names, so their paths are replaced with a stable hash (the domain and the hash stay, so dedupe and
attribution still line up).

Usage: python3 export_data.py <local run dir with runs/ and grading/> <stage label, e.g. stage-a>
The redaction helpers (redact_url, redact_text, deep_redact, is_unhashed_social) can be imported: Stage C's score_c.py
uses them, so every stage hashes social paths the same way.
"""
import hashlib, json, pathlib, re, shutil, sys
from urllib.parse import urlsplit

SRC = STAGE = DST = None  # set by main() from the command line, so that importing this module has no side effects

SOCIAL = {"linkedin.com", "x.com", "twitter.com", "facebook.com", "instagram.com", "tiktok.com", "threads.net"}
URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")


def redact_url(u):
    if not isinstance(u, str) or "://" not in u:
        return u
    try:
        s = urlsplit(u)
    except Exception:
        return u
    host = (s.hostname or "").lower()
    base = host[4:] if host.startswith("www.") else host
    dom = next((d for d in SOCIAL if base == d or base.endswith("." + d)), None)
    path = s.path or ""
    if dom is None and base.endswith("reddit.com") and re.match(r"^/(u|user)/", path):
        dom = "reddit.com"
    if dom is None and base.endswith("youtube.com") and re.match(r"^/(@|c/|channel/|user/)", path):
        dom = "youtube.com"
    if dom is None:
        return u
    h = hashlib.sha256((path + "?" + (s.query or "")).encode()).hexdigest()[:12]
    return f"{s.scheme}://{host}/[redacted-{h}]"


BARE_RE = re.compile(r"(?<![\w/.:])((?:[a-z0-9-]+\.)*(?:linkedin\.com|x\.com|twitter\.com|facebook\.com|instagram\.com|tiktok\.com"
                     r"|threads\.net|reddit\.com/(?:u|user)|youtube\.com/(?:@|c/|channel/|user/))[^\s\"'<>)\]|]*)", re.I)


HANDLE_RE = re.compile(r"(?<![\w@.])@[A-Za-z0-9_]{2,30}\b")


def redact_bare(m):
    # Normalized keys drop the scheme ("linkedin.com/posts/..."); redact them the same way.
    red = redact_url("https://" + m.group(1))
    return red[len("https://"):] if red.startswith("https://") else red


PERSONAL_HANDLES = {"marcopesani": "[user]"}  # personal GitHub handles seen in judge notes (review F11)


def redact_text(t):
    if not isinstance(t, str):
        return t
    for h, r in PERSONAL_HANDLES.items():
        t = t.replace(h, r)
    t = URL_RE.sub(lambda m: redact_url(m.group(0)), t)
    t = BARE_RE.sub(redact_bare, t)
    return HANDLE_RE.sub("@[handle]", t)  # social handles in judge notes


def deep_redact(x):
    if isinstance(x, dict):
        return {redact_text(k): deep_redact(v) for k, v in x.items()}
    if isinstance(x, list):
        return [deep_redact(v) for v in x]
    return redact_text(x)


def is_unhashed_social(text):
    """True if the text carries a social-media profile or post URL whose path isn't hashed (a check for exporters)."""
    if not isinstance(text, str):
        return False
    for m in URL_RE.finditer(text):
        u = m.group(0)
        if "/[redacted-" not in u and redact_url(u) != u:
            return True
    for m in BARE_RE.finditer(text):
        if "[redacted-" not in m.group(1) and redact_bare(m) != m.group(1):
            return True
    return False


def domain(u):
    try:
        h = (urlsplit(u).hostname or "").lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def clean_detail(d):
    """Keep crawl status metadata only."""
    keep = {"id", "url", "status", "error", "tag", "httpStatusCode", "source", "code"}
    if isinstance(d, list):
        return [clean_detail(v) for v in d]
    if isinstance(d, dict):
        return {k: clean_detail(v) for k, v in d.items() if k in keep}
    return d


def clean_record(r):
    out = {k: r[k] for k in ("set", "id", "provider", "ts", "status", "latency_ms", "diag", "variant", "action",
                             "terminal_status", "stop_reason", "enrich_status") if k in r}
    c = r.get("cost") or {}
    out["cost"] = {k: c[k] for k in ("usd", "credits", "credits_billed", "paid_run", "basis", "matched", "candidates", "usage") if k in c}
    if "request" in r:
        out["request"] = r["request"]
    if "results" in r:
        out["results"] = [{"rank": x.get("rank"), "url": x.get("url"), "domain": domain(x.get("url") or ""),
                           "published": x.get("published"), "text_chars": len(x.get("text") or ""),
                           **({"scraped": True} if x.get("scraped") else {}),
                           **({"scrape_skipped": x["scrape_skipped"]} if x.get("scrape_skipped") else {})}
                          for x in r["results"]]
    if "content" in r or "content_chars" in r:
        out["content_chars"] = r.get("content_chars", len(r.get("content") or ""))
        out["detail"] = clean_detail(r.get("detail"))
    for k in ("phrases_found", "recovered"):
        if k in r:
            out[k] = r[k]
    return deep_redact(out)


# Stage B allowlist (Amendment 7, review F11): each grading file type keeps only these keys; anything else is dropped,
# and a file that matches no rule isn't exported. Provider-written values (S6 cell values, S5 claimed fields) never
# appear here; the verdicts on them do. Judge notes may quote short fragments (under 15 words) of pages or values.
ALLOW_B = [
    (re.compile(r"^S6_c\d+_judge\.json$"), {"": ["set", "cells"], "cells": ["cell", "values"], "cells.values": ["vid", "verdict", "citation_valid", "novel", "checked_url", "note"]}),
    (re.compile(r"^S6_pool\.json$"), {"": ["cell", "company", "homepage", "field", "definition", "kb_ground_truth", "values"], "values": ["vid", "source_url"]}),
    (re.compile(r"^S5_S5[abc]_judge\.json$"), {"": ["set", "list", "entities"], "entities": ["eid", "criteria", "overall", "fields", "novelty", "checked_url", "note"]}),
    (re.compile(r"^S5_S5[abc]_pool\.json$"), {"": ["list", "entities"], "entities": ["eid", "key", "names", "urls", "evidence_urls", "matches_exclusion", "claimed_fields"]}),
    (re.compile(r"^SP_judge\.json$"), {"": ["set", "domains"], "domains": ["domain", "class", "sells", "in_kb", "note"]}),
    (re.compile(r"^SP_pool\.json$"), {"": ["domains"], "domains": ["domain", "urls"]}),
    (re.compile(r"^(S5_meta|S6_meta|scored_b|tables_stage_b|S6_nogt_kb_check|S6_overrides|S6_correction_types|S5_posthoc|S5_url_completeness)\.json$"), None),
    (re.compile(r"^S5_S5[abc]_attribution\.json$|^S6_attribution\.json$"), None),
]


def _allow(data, rules, path=""):
    keep = rules.get(path)
    if isinstance(data, list):
        return [_allow(x, rules, path) for x in data]
    if isinstance(data, dict) and keep is not None:
        return {k: _allow(v, rules, (path + "." + k).lstrip(".")) for k, v in data.items() if k in keep}
    return data


def export_stage_b(g):
    """Stage B grading files, through the allowlist above. S5 pools keep claimed field names only, not values."""
    b, out = g / "b", DST / "grading" / "b"
    (out / "_attrib").mkdir(parents=True)
    dump = lambda path, data: path.write_text(json.dumps(deep_redact(data), indent=1, ensure_ascii=False))
    for p in sorted(b.glob("*.json")) + sorted((b / "_attrib").glob("*.json")):
        rule = next((r for pat, r in ALLOW_B if pat.match(p.name)), "none")
        if rule == "none":
            if not (p.name.startswith("S6_c") and p.name.endswith("_pool.json")):  # chunk pools duplicate S6_pool.json
                print(f"not exported (no allowlist rule): {p.name}")
            continue
        data = json.loads(p.read_text())
        if p.name.startswith("S5_S5") and p.name.endswith("_pool.json"):
            for e in data["entities"]:
                e["claimed_fields"] = sorted(e.pop("claimed", {}))  # field names only, not the values
        dump(out / p.relative_to(b), _allow(data, rule) if rule else data)
    for name in ["SP_domains.json"]:
        if (g / name).exists():
            dump(DST / "grading" / name, json.loads((g / name).read_text()))


def main():
    global SRC, STAGE, DST
    SRC = pathlib.Path(sys.argv[1]).resolve()
    STAGE = sys.argv[2]
    DST = pathlib.Path(__file__).resolve().parent.parent / "data" / STAGE
    if DST.exists():
        shutil.rmtree(DST)
    (DST / "runs").mkdir(parents=True)
    (DST / "grading").mkdir(parents=True)
    n = 0
    sets = {"stage-a": ["S1", "S2", "S3", "S4", "diag"], "stage-b": ["S5", "S6", "SP", "SM"]}[STAGE]
    files = [f for s_ in sets for f in sorted((SRC / "runs" / s_).glob("*.jsonl"))]
    for f in files:
        rel = f.relative_to(SRC / "runs")
        out = DST / "runs" / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(f, encoding="utf-8") as fin, open(out, "w", encoding="utf-8") as fout:
            for line in fin:
                fout.write(json.dumps(clean_record(json.loads(line)), ensure_ascii=False) + "\n")
                n += 1
    extras = (["ledger.json"] + [x.name for x in (SRC / "runs").glob("log-*.txt")]) if STAGE == "stage-a" else ["ledger_b.json"]
    for extra in extras:
        if (SRC / "runs" / extra).exists():
            (DST / "runs" / extra).write_text(redact_text((SRC / "runs" / extra).read_text()))
    g = SRC / "grading"
    if STAGE == "stage-b":
        export_stage_b(g)
        print(f"exported {n} run records to {DST}")
        return
    grading_files = ["S1_judge.json", "S1_judge_b.json", "S4_judge.json", "S1_attribution.json", "S4_attribution.json",
                     "summary_stageA_scripted.json", "S1_scored_first20.json", "S1_scored.json", "S4_scored.json",
                     "S4_facts_scored.json", "tables_stage_a.json"]
    for name in grading_files:
        if (g / name).exists():
            (DST / "grading" / name).write_text(json.dumps(deep_redact(json.loads((g / name).read_text())), indent=1, ensure_ascii=False))
    for s in ["S1", "S4"]:
        p = g / f"{s}_pools_blind.json"
        if p.exists():
            pools = json.loads(p.read_text())
            for q in pools:
                q["results"] = [{"rid": r["rid"], "key": r["key"], "url": r["url"], "published": r.get("published"), "in_kb": r.get("in_kb")}
                                for r in q["results"]]
            (DST / "grading" / f"{s}_pools_urls.json").write_text(json.dumps(deep_redact(pools), indent=1, ensure_ascii=False))
    v2 = g / "v2"
    if v2.exists():
        (DST / "grading" / "v2").mkdir()
        for p in sorted(v2.glob("*.json")) + sorted((v2 / "_attrib").glob("*.json")):
            data = json.loads(p.read_text())
            if p.name.endswith("_pools_blind.json") or p.name.endswith("_pool.json"):
                if not p.name.endswith("_pools_blind.json"):
                    continue  # chunk files duplicate the full pools
                for q in data:
                    q.pop("kb_context_at_stage_a", None)
                    q["results"] = [{"rid": r["rid"], "key": r["key"], "url": r["url"], "published": r.get("published"), "in_kb": r.get("in_kb")}
                                    for r in q["results"]]
                name = p.name.replace("_pools_blind.json", "_pools_urls.json")
            else:
                name = p.name
            (DST / "grading" / "v2" / name).write_text(json.dumps(deep_redact(data), indent=1, ensure_ascii=False))
    print(f"exported {n} run records to {DST}")


if __name__ == "__main__":
    main()
