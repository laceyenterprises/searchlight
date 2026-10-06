#!/usr/bin/env python3
"""Score Stage B (Amendment 5) from the blind judge files, unblinded through grading/b/_attrib/.

S6 (schema fill), per provider:
- completeness: non-null values ÷ 160 cells;
- on ground-truth cells: correct, partly correct, wrong, and correction (a verified disagreement with the KB);
- on cells without ground truth: verified, partly verified, wrong, unverifiable;
- false-claim rate: wrong ÷ non-null values;
- citation validity: yes ÷ (yes + no) over the citations the judges checked;
- new facts for the KB: values judged `correction` or `verified` (novel), plus the ones only that provider had right.
S5 (entity lists), per provider: returned, meets, partial, fails, unverifiable, precision (meets ÷ returned),
verified novel entities, novel entities no other provider returned, and returned entities from the exclusion list.
Costs come from runs/ledger_b.json and the run records.

Post hoc (Amendment 6):
- grading/b/S6_nogt_kb_check.json marks the empty cells whose fact the KB already states; `novel_kb_new` leaves them out.
- MERGE merges two same-company pairs the S5 dedupe missed.
Post hoc (Amendment 7, after the PR #25 Stage B review). The as-judged figures stay in the output next to each
adjusted one:
- grading/b/S6_overrides.json: one verdict change, and the non-responsive values;
- grading/b/S6_correction_types.json: what each correction does to the KB (substantive or not);
- grading/b/S5_posthoc.json: the fact-level novelty rule (primary), the entity-level rule (sensitivity), and fixes to
  three exclusion flags.

Usage: python3 score_b.py [--data DIR]   (writes grading/b/scored_b.json under DIR, default this folder;
       --data ../data/stage-b re-scores from the committed, sanitized data)
"""
import collections, json, pathlib, sys

ROOT = pathlib.Path(sys.argv[sys.argv.index("--data") + 1]).resolve() if "--data" in sys.argv else pathlib.Path(__file__).resolve().parent
B = ROOT / "grading" / "b"
P = ["exa", "parallel", "firecrawl"]


def jload(name, default=None):
    f = B / name
    return json.loads(f.read_text()) if f.exists() else default


def s6():
    attrib = jload("_attrib/S6_attribution.json")
    pool = {i["cell"]: i for i in jload("S6_pool.json")}
    judged = {}
    for f in sorted(B.glob("S6_c*_judge.json")):
        for c in json.loads(f.read_text())["cells"]:
            for v in c["values"]:
                judged[v["vid"]] = v
    chk = jload("S6_nogt_kb_check.json", {"cells": {}})["cells"]
    kb_states = {c for c, v in chk.items() if v["kb_states_it"]}
    ov = jload("S6_overrides.json", {"verdicts": {}, "non_responsive": {}})
    ctypes = jload("S6_correction_types.json", {"cells": {}})["cells"]
    st = {p: collections.Counter() for p in P}
    novel_cells, values = collections.defaultdict(set), []
    for cell, item in pool.items():
        has_gt = bool(item.get("kb_ground_truth"))
        for v in item["values"]:
            p = attrib[v["vid"]]
            j = judged.get(v["vid"])
            st[p]["non_null"] += 1
            if not j:
                st[p]["unjudged"] += 1
                continue
            vj = j.get("verdict")
            verdict = ov["verdicts"].get(v["vid"], {}).get("to", vj)
            nonresp = v["vid"] in ov["non_responsive"]
            st[p][("gt_" if has_gt else "nogt_") + verdict] += 1
            st[p][("judged_gt_" if has_gt else "judged_nogt_") + vj] += 1
            if verdict == "wrong":
                st[p]["wrong"] += 1
                if nonresp:
                    st[p]["wrong_non_responsive"] += 1
            cv = j.get("citation_valid")
            if cv in ("yes", "no"):
                st[p]["cite_" + cv] += 1
                if verdict != "wrong":
                    st[p]["cite_" + cv + "_not_wrong"] += 1
                elif cv == "yes":
                    st[p]["wrong_with_valid_citation"] += 1
            novel = verdict == "correction" or (not has_gt and verdict == "verified")
            if novel:
                st[p]["novel"] += 1
                novel_cells[cell].add(p)
                if cell not in kb_states:
                    st[p]["novel_kb_new"] += 1
                if verdict == "correction":
                    st[p]["correction_" + ctypes.get(cell, {}).get("type", "untyped")] += 1
            values.append({"vid": v["vid"], "cell": cell, "company_id": cell.split(".")[0], "field": item["field"], "provider": p,
                           "has_gt": has_gt, "verdict_judged": vj, "verdict": verdict, "non_responsive": nonresp,
                           "citation_valid": cv, "novel": novel, "kb_states_it": cell in kb_states})
    for cell, ps in novel_cells.items():
        if len(ps) == 1:
            p = next(iter(ps))
            st[p]["novel_unique"] += 1
            if cell not in kb_states:
                st[p]["novel_kb_new_unique"] += 1
    gt_cells = sum(1 for i in pool.values() if i.get("kb_ground_truth"))
    out = {}
    for p in P:
        s = st[p]
        right_gt = s["gt_correct"] + s["gt_correction"]
        wr, nn = s["wrong"] - s["wrong_non_responsive"], s["non_null"] - s["wrong_non_responsive"]
        out[p] = {**dict(s),
                  "completeness": f"{s['non_null']}/160",
                  "gt_right_of_gt_cells": f"{right_gt}/{gt_cells}",
                  "gt_right_of_gt_cells_as_judged": f"{s['judged_gt_correct'] + s['judged_gt_correction']}/{gt_cells}",
                  "gt_right_incl_partly": f"{right_gt + s['gt_partly_correct']}/{gt_cells}",
                  "false_claim_rate": round(s["wrong"] / s["non_null"], 3) if s["non_null"] else None,
                  "false_claim_rate_excl_non_responsive": round(wr / nn, 3) if nn else None,
                  "citation_validity": f"{s['cite_yes']}/{s['cite_yes'] + s['cite_no']}",
                  "citation_validity_not_wrong": f"{s['cite_yes_not_wrong']}/{s['cite_yes_not_wrong'] + s['cite_no_not_wrong']}"}
    corr_cells = {c for c, i in pool.items() if i.get("kb_ground_truth") and c in novel_cells}
    out["novel_cells_any_provider"] = len(novel_cells)
    out["novel_cells_kb_new"] = len([c for c in novel_cells if c not in kb_states])
    out["correction_cells"] = len(corr_cells)
    out["correction_cells_by_type"] = dict(collections.Counter(ctypes.get(c, {}).get("type", "untyped") for c in corr_cells))
    out["gt_cells"] = gt_cells
    out["values"] = values
    return out


# Post-hoc merges (Amendment 6): the pool's domain dedupe missed two same-company pairs, which the judges flagged
# (Sana under sanalabs.com and sana.ai; Coherence with and without its domain).
MERGE = {"S5b": [["S5b-e2", "S5b-e6"]], "S5c": [["S5c-e13", "S5c-e26"]]}
RULES = ("fact_level", "entity_level", "as_judged")   # fact_level is primary (Amendment 7)


def novelty(e, ph):
    eid, judged = e["eid"], e.get("novelty") == "novel"
    return {"as_judged": judged,
            "fact_level": judged or eid in ph.get("fact_level_to_novel", {}),
            "entity_level": (judged or eid in ph.get("entity_level_to_novel", {})) and eid not in ph.get("entity_level_to_known", {})}


def s5():
    ph = jload("S5_posthoc.json", {})
    fixes = ph.get("exclusion_flag_fixes", {})
    out = {}
    for lid in ["S5a", "S5b", "S5c"]:
        jf = B / f"S5_{lid}_judge.json"
        if not jf.exists():
            continue
        j = json.loads(jf.read_text())
        if isinstance(j, list):
            j = next(x for x in j if x.get("list") == lid)
        attrib = jload(f"_attrib/S5_{lid}_attribution.json")
        pool = {e["eid"]: e for e in jload(f"S5_{lid}_pool.json")["entities"]}
        st = {p: collections.Counter() for p in P}
        group = {}
        for g in MERGE.get(lid, []):
            for eid in g:
                group[eid] = set().union(*[set(attrib.get(x, [])) for x in g])
        dup = {g[1] for g in MERGE.get(lid, [])}
        any_new = collections.Counter()
        for e in j["entities"]:
            eid = e["eid"]
            ps = attrib.get(eid, [])
            finders = group.get(eid, set(ps))
            excl = fixes[eid]["to"] if eid in fixes else pool[eid].get("matches_exclusion")
            nov = novelty(e, ph)
            meets = e["overall"] == "meets"
            for rule in RULES:
                if meets and nov[rule] and eid not in dup:
                    any_new[rule] += 1
            for p in ps:
                st[p]["returned"] += 1
                st[p][e["overall"]] += 1
                if pool[eid].get("matches_exclusion"):
                    st[p]["returned_excluded_as_flagged"] += 1
                if excl:
                    st[p]["returned_excluded"] += 1
                for rule in RULES:
                    sfx = "" if rule == "fact_level" else "_" + rule
                    if meets and nov[rule]:
                        st[p]["verified_novel" + sfx] += 1
                        if len(finders) == 1:
                            st[p]["verified_novel_unique" + sfx] += 1
        out[lid] = {p: {**dict(st[p]), "precision": f"{st[p]['meets']}/{st[p]['returned']}"} for p in P}
        out[lid]["pooled"] = len(j["entities"])
        out[lid]["pooled_after_merge"] = len(j["entities"]) - len(dup)
        out[lid]["verified_novel_any"] = any_new["fact_level"]
        out[lid]["verified_novel_any_entity_level"] = any_new["entity_level"]
        out[lid]["verified_novel_any_as_judged"] = any_new["as_judged"]
    return out


def costs():
    c = {"S5": collections.defaultdict(float), "S6": collections.defaultdict(float), "SP": collections.defaultdict(float),
         "fc_credits": collections.defaultdict(int)}
    for s in ["S5", "S6", "SP"]:
        for p in P:
            f = ROOT / "runs" / s / f"{p}.jsonl"
            if not f.exists():
                continue
            for line in open(f):
                r = json.loads(line)
                if r.get("status") == "skipped_cap":
                    continue
                k = r.get("cost") or {}
                c[s][p] += k.get("usd") or 0.0
                if p == "firecrawl":
                    c["fc_credits"][s] += k.get("credits_billed") or 0
    return {k: dict(v) for k, v in c.items()}


def main():
    out = {"S6": s6(), "S5": s5(), "costs": costs()}
    (B / "scored_b.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out["S6"].items() if k != "values"}, indent=1))
    print(json.dumps(out["S5"], indent=1))


if __name__ == "__main__":
    main()
