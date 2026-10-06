#!/usr/bin/env python3
"""Harvest the Stage B Monitors probe (SM) from Exa, then pause the monitors. Run 2026-10-06.

Replaces monitors_b.py's poll/delete: the monitors were found by name (their IDs weren't kept), harvested, and
paused rather than deleted, so their history stays in the Exa account. The key comes from the environment only.
Raw API responses stay in runs/SM_raw/ (gitignored); ../data/stage-b/runs/SM/ holds the sanitized record, built
with export_data.py's redaction helpers.

Usage: python3 monitors_harvest.py harvest | export | pause | status   (export rebuilds the record from runs/SM_raw/)
"""
import json, os, pathlib, sys, time, urllib.error, urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from export_data import deep_redact, domain  # noqa: E402  the experiment's redaction helpers

API = "https://api.exa.ai"
PREFIX = "provider-eval SM "
SM = json.loads((HERE / "sets" / "SM.json").read_text())
RAW, OUT = HERE / "runs" / "SM_raw", HERE.parent / "data" / "stage-b" / "runs" / "SM"


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": "Bearer " + os.environ["EXA_API_KEY"], "Content-Type": "application/json",
        "User-Agent": "kb-provider-eval/0.3"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(2 ** attempt)
                continue
            return e.code, {"_body": e.read()[:300].decode("utf-8", "ignore")}
    return 599, {"_body": "retries exhausted"}


def paged(path):
    items, cursor = [], None
    while True:
        st, j = call("GET", path + ("&" if "?" in path else "?") + "limit=100" + (f"&cursor={cursor}" if cursor else ""))
        if st != 200:
            raise SystemExit(f"GET {path} -> {st} {j}")
        items += j.get("data") or []
        if not j.get("hasMore"):
            return items
        cursor = j.get("nextCursor")


def ours():
    """The probe's monitors, keyed by set id (m01..m05), matched on the names the harness gave them."""
    by_name = {m["name"]: m["id"] for m in SM["monitors"]}
    found = {}
    for m in paged("/monitors"):
        if m.get("name", "").startswith(PREFIX) and m["name"] in by_name:
            if by_name[m["name"]] in found:
                raise SystemExit(f"two monitors named {m['name']!r}; resolve by hand")
            found[by_name[m["name"]]] = m
    return dict(sorted(found.items()))


def harvest():
    RAW.mkdir(parents=True, exist_ok=True); OUT.mkdir(parents=True, exist_ok=True)
    mons = ours()
    print(f"{len(mons)} of {len(SM['monitors'])} probe monitors found")
    summary = {"harvested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "monitors": []}
    for sid, m in mons.items():
        xid = m["id"]
        st, detail = call("GET", f"/monitors/{xid}")
        detail.pop("webhookSecret", None)  # never stored
        runs = paged(f"/monitors/{xid}/runs")
        full, bad = [], 0
        for r in runs:
            s2, rj = call("GET", f"/monitors/{xid}/runs/{r['id']}")
            if s2 != 200:
                bad += 1
                rj = {**r, "_fetch_status": s2}
            full.append(rj)
        (RAW / f"{sid}.json").write_text(json.dumps({"monitor": detail, "runs": full}, indent=1))
        print(f"{sid} {detail.get('status')} runs={len(runs)} fetched_ok={len(runs) - bad}")
        summary["monitors"].append({"id": sid, "status_at_harvest": detail.get("status"), "runs": len(runs),
                                    "run_fetch_failures": bad})
    (RAW / "harvest_summary.json").write_text(json.dumps(summary, indent=1))
    export()


def clean(sid, detail, full):
    """Sanitized record: no Exa IDs and no vendor-written text (titles, change summaries, run notes); those keep
    their lengths only, as export_data.clean_record does for search runs. URLs pass through deep_redact."""
    runs = []
    for i, r in enumerate(sorted(full, key=lambda x: x.get("createdAt") or ""), start=1):
        out = r.get("output") or {}
        runs.append({
            "run": i, "status": r.get("status"), "fail_reason": r.get("failReason"),
            **{k: r.get(k) for k in ("createdAt", "startedAt", "completedAt", "failedAt", "cancelledAt", "durationMs")},
            "note_chars": len(out.get("content") or ""),
            "results": [{"rank": n, "url": x.get("url"), "domain": domain(x.get("url") or ""),
                         "change_type": x.get("changeType"), "change_fingerprint": x.get("changeFingerprint"),
                         "title_chars": len(x.get("title") or ""), "summary_chars": len(x.get("summary") or "")}
                        for n, x in enumerate(out.get("results") or [], start=1)],
        })
    return deep_redact({
        "set": "SM", "id": sid, "provider": "exa", "name": detail.get("name"), "status_at_harvest": detail.get("status"),
        "search": detail.get("search"), "trigger": detail.get("trigger"), "output_schema": detail.get("outputSchema"),
        "created": detail.get("createdAt"), "runs": runs,
    })


def export():
    OUT.mkdir(parents=True, exist_ok=True)
    meta = json.loads((RAW / "harvest_summary.json").read_text())
    rows = []
    for f in sorted(RAW.glob("m0*.json")):
        sid, d = f.stem, json.loads(f.read_text())
        record = clean(sid, d["monitor"], d["runs"])
        (OUT / f"runs_{sid}.json").write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n")
        changed = [r for r in record["runs"] if r["results"]]
        rows.append({"id": sid, "name": record["name"], "runs": len(record["runs"]),
                     "runs_completed": sum(r["status"] == "completed" for r in record["runs"]),
                     "runs_with_changes": len(changed), "change_results": sum(len(r["results"]) for r in changed),
                     "distinct_fingerprints": len({x["change_fingerprint"] for r in changed for x in r["results"]}),
                     "first_run": record["runs"][0]["createdAt"] if record["runs"] else None,
                     "last_run": record["runs"][-1]["createdAt"] if record["runs"] else None})
        print(rows[-1])
    (OUT / "summary.json").write_text(json.dumps({
        "harvested_at": meta["harvested_at"], "paused_at_harvest": True,
        "note": "Harvested and paused on the harvest date, four days before the planned 2026-10-10 end.",
        "monitors": rows}, indent=1) + "\n")


def pause():
    for sid, m in ours().items():
        if m.get("status") == "paused":
            print(sid, "already paused")
            continue
        st, j = call("PATCH", f"/monitors/{m['id']}", {"status": "paused"})
        print(sid, "pause ->", st, j.get("status"), "" if st == 200 else j)


def status():
    for sid, m in ours().items():
        print(sid, m.get("status"), "next run:", m.get("nextRunAt"))


if __name__ == "__main__":
    {"harvest": harvest, "export": export, "pause": pause, "status": status}[sys.argv[1]]()
