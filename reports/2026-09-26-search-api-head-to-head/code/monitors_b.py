#!/usr/bin/env python3
"""Stage B Exa-only Monitors probe (SM). Create, poll, or delete the five monitors in sets/SM.json.

Keys come from the environment only (op run). The webhook subscribes to monitor.deleted only, pointed at a placeholder,
so run results are never delivered anywhere; this script polls them through the API.

Usage: python3 monitors_b.py create | poll | delete
"""
import json, pathlib, sys, time, os, urllib.request

import harness as A

ROOT = pathlib.Path(__file__).resolve().parent
SM = json.loads((ROOT / "sets" / "SM.json").read_text())
OUT = ROOT / "runs" / "SM"
OUT.mkdir(parents=True, exist_ok=True)
IDS = OUT / "monitors.json"
H = lambda: {"Authorization": "Bearer " + os.environ["EXA_API_KEY"]}


def get(url):
    req = urllib.request.Request(url, headers={**H(), "User-Agent": "kb-provider-eval/0.2"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {"_body": e.read()[:300].decode("utf-8", "ignore")}


def create():
    ids = json.loads(IDS.read_text()) if IDS.exists() else {}
    for m in SM["monitors"]:
        if m["id"] in ids:
            continue
        body = {"name": m["name"], "search": {"query": m["query"], "numResults": SM["num_results"], "includeDomains": m["include_domains"]},
                "trigger": SM["trigger"], "webhook": SM["webhook"],
                "metadata": {"experiment": "provider-eval", "stage": "B", "probe": "SM", "delete_after": SM["delete_after"]}}
        st, txt, ms = A.post("https://api.exa.ai/monitors", H(), body, timeout=60, pace=A.PACE["exa"])
        j = A.parse(txt)
        j.pop("webhookSecret", None)  # never stored
        print(m["id"], st, j.get("id"), j.get("status"), (txt[:200] if st >= 300 else ""))
        if st in (200, 201) and j.get("id"):
            ids[m["id"]] = j["id"]
        with open(OUT / "exa.jsonl", "a") as f:
            f.write(json.dumps({"set": "SM", "id": m["id"], "provider": "exa", "action": "create", "ts": time.time(), "status": st,
                                "monitor_id": j.get("id"), "request": body, "response": j}) + "\n")
    IDS.write_text(json.dumps(ids, indent=1))


def poll():
    ids = json.loads(IDS.read_text())
    for mid, xid in ids.items():
        st, j = get(f"https://api.exa.ai/monitors/{xid}/runs?limit=100")
        runs = j.get("data") or j.get("runs") or []
        full = []
        for r in runs:
            s2, rj = get(f"https://api.exa.ai/monitors/{xid}/runs/{r['id']}")
            full.append(rj if s2 == 200 else {"id": r.get("id"), "_status": s2})
        (OUT / f"runs_{mid}.json").write_text(json.dumps(full, indent=1))
        print(mid, st, len(runs), "runs;", sum(len(((x.get("output") or {}).get("results") or [])) for x in full), "results")


def delete():
    ids = json.loads(IDS.read_text())
    for mid, xid in ids.items():
        req = urllib.request.Request(f"https://api.exa.ai/monitors/{xid}", method="DELETE", headers={**H(), "User-Agent": "kb-provider-eval/0.2"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                print(mid, "deleted", r.status)
        except urllib.error.HTTPError as e:
            print(mid, "delete failed", e.code)


if __name__ == "__main__":
    {"create": create, "poll": poll, "delete": delete}[sys.argv[1]]()
