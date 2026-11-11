"""Package a graded insight run as a shareable study under ``reports/<yyyy-mm-dd>-<slug>/``.

The bundle follows the 2026-09 head-to-head precedent: the report (summary.json,
REPORT.md, dashboard.html), the question set, the frozen profiles, generated
methodology and reproduction notes, and sanitized data.

Sanitizing keeps claims, URLs, verdicts, values, metadata and short excerpts
(at most 300 characters). It drops full page text and full vendor answer
bodies: captured pages are never copied, a cell's raw response becomes a
``local_only`` stub, and every link to a dropped file reads ``local-only:``.
Credentials were never written in the first place; headers keep their slots.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Mapping

from ..catalog import module_root
from .catalog import SET_DIR, InsightError, load_set
from .report import LOCAL_ONLY, score, write_bundle
from .run import read_json, write_json

SLUG = re.compile(r"\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]{1,60}")
EXCERPT = 300
COPIED = ("manifest.json", "ledger.json", "query-writer", "grading/grading.json", "grading/extract",
          "grading/clusters", "grading/verify", "grading/judge", "cells")


def excerpt(text: Any) -> str:
    text = str(text or "")
    return text if len(text) <= EXCERPT else text[: EXCERPT - 1].rstrip() + "…"


def sanitize_cell(cell: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(cell)
    if "response" in out:
        raw = out["response"]
        out["response"] = {"local_only": True,
                           "chars": len(raw if isinstance(raw, str) else json.dumps(raw, default=str))}
    out["items"] = [{**{k: v for k, v in item.items() if k != "page_text"}, "text": excerpt(item.get("text"))}
                    for item in cell.get("items") or []]
    return out


def relink(value: Any) -> Any:
    """Report links (``../X``) become bundle links (``data/X``), or ``local-only:X``."""
    if isinstance(value, str) and value.startswith("../"):
        target = value[3:]
        kept = any(target == c or target.startswith(c + "/") for c in COPIED)
        return f"data/{target}" if kept else f"{LOCAL_ONLY}{target}"
    if isinstance(value, Mapping):
        return {k: relink(v) for k, v in value.items()}
    if isinstance(value, list):
        return [relink(v) for v in value]
    return value


def _methodology(summary: Mapping[str, Any], manifest: Mapping[str, Any]) -> str:
    run, s = summary["run"], summary["set"]
    lines = [f"# Methodology: {s['title']} ({run['run_date']})", "",
             "This study ran the Searchlight insight bench (INS). Every question in the set went to every arm "
             "on the same day, with each vendor's request frozen in a profile taken from that vendor's own "
             "documentation. One fixed extractor model turned each arm's returned content into atomic claims; "
             "claims were pooled and clustered per question; each claim was checked on the page it cites; and "
             "a blind judge valued the verified clusters 0-3 against the reader context. The bench's README "
             "and provider best-practices page are `docs/insight-bench/` in the Searchlight repository.", "",
             "## Scoring", "",
             "- **Strict credit.** An arm earns a cluster only when one of its own claims there is supported by "
             "the page that claim cites. A claim with no source counts as unsupported.",
             "- **Insight** is an earned cluster valued 1-3 (3 decision-changing, 2 new design detail, "
             "1 corroborating); **unique** means exactly one arm earned it.",
             "- **Intervals.** Counts carry 95% question-bootstrap intervals, and the unsupported rate a Wilson "
             f"interval; the leader is compared with each arm by an exact sign test with Holm adjustment.", "",
             "## Grading", "",
             f"- Extractor: {run['extractor']}; judge: {run['judge']}; blinding seed {run['seed']}.",
             "- Judging payloads carried no arm labels, the value judge saw clusters in a seeded shuffle "
             "without URLs, and tested vendors' brand names were redacted from findings.", "",
             "## Arms", "", "| Arm | Kind | Profile sha256 | Unverified fields |", "| --- | --- | --- | ---: |"]
    for arm in summary["arms"]:
        kind = "imported" if arm["imported"] else arm["kind"]
        lines.append(f"| {arm['arm_id']} | {kind} | `{arm['profile_sha256'][:16]}` | {len(arm['unverified'])} |")
    lines += ["", "Profiles are in [profiles/](profiles/_shared.json) and the question set in "
              f"[set/](set/{s['set_id']}.json).", "", "## Caveats", ""] + [f"- {c}" for c in summary["caveats"]]
    lines += ["", "## Published data", "",
              "`data/` holds the run manifest, the spend ledger, the shared query-writer output, every cell's "
              "request and metadata, and every grading file (claims, clusters, verdicts, values). Full page text "
              f"and full vendor answer bodies are not published: result text is cut to {EXCERPT}-character "
              "excerpts, a cell's raw response is replaced by its length, and captured pages stay on the "
              "machine that ran the study; links to them read *local only*."]
    return "\n".join(lines) + "\n"


def _reproduce(summary: Mapping[str, Any], manifest: Mapping[str, Any]) -> str:
    run, s = summary["run"], summary["set"]
    own = [a["arm_id"] for a in summary["arms"] if not a["imported"]]
    model = (manifest.get("extractor_model") or "MODEL_ID")
    judge = (run.get("judge") or {}).get("model") or "MODEL_ID"
    lines = [f"# Reproduce: {s['title']}", "",
             "A new live run evaluates the method again: the web, the vendors' indexes and the models change, "
             "so its numbers will differ. These are the inputs and commands this run used.", "",
             "| Input | sha256 |", "| --- | --- |", f"| question set `{s['set_id']}` | `{run['set_sha256']}` |",
             f"| shared templates `_shared.json` | `{run.get('shared_sha256')}` |"]
    lines += [f"| profile `{a['arm_id']}` | `{a['profile_sha256']}` |" for a in summary["arms"]]
    lines += ["", "```sh",
              f"sew insight run --set {s['set_id']} --arms {','.join(own)} --extractor-model {model} \\",
              f"  --max-usd {summary['spend'].get('max_usd') or 'N'} --dry-run   # then again with SEW_HARNESS_LIVE=1"]
    for i in summary["imported"]:
        lines.append(f"sew insight import --run RUN --arm {i['arm_id']} --claims CLAIMS.jsonl "
                     f"--label {json.dumps(i['label'])} --note {json.dumps(i['note'] or '')}")
    lines += [f"SEW_HARNESS_LIVE=1 sew insight grade --run RUN --judge-model {judge}",
              "sew insight report --run RUN", "sew insight publish --run RUN --to reports/YYYY-MM-DD-SLUG/", "```",
              "", "Keys come from `SEW_*_API_KEY` variables or `*_API_KEY_REF` references; none are stored here."]
    return "\n".join(lines) + "\n"


def _link_from_index(index: Path, to: Path, summary: Mapping[str, Any]) -> None:
    if not index.is_file():
        return
    text = index.read_text(encoding="utf-8")
    if f"({to.name}/REPORT.md)" in text:
        return
    line = (f"- [{summary['set']['title']}]({to.name}/REPORT.md): an INS insight bake-off, "
            f"{len(summary['arms'])} arms x {summary['set']['questions']} questions on {summary['run']['run_date']}, "
            f"graded blind and verified on each claim's own page; [interactive dashboard]({to.name}/dashboard.html).")
    rows = text.split("\n")
    last = max((i for i, r in enumerate(rows) if r.startswith("- [")), default=len(rows) - 1)
    rows.insert(last + 1, line)
    index.write_text("\n".join(rows), encoding="utf-8")


def publish(run_dir: Path, to: Path, *, set_root: Path | None = None) -> dict[str, Any]:
    if not SLUG.fullmatch(to.name) or to.parent.name != "reports":
        raise InsightError(f"publish to reports/<yyyy-mm-dd>-<slug>/, not {to}")
    if to.exists() and any(to.iterdir()):
        previous = to / "summary.json"
        if not (previous.is_file() and read_json(previous).get("schema") == "sew-insight-summary"):
            raise InsightError(f"{to} exists and is not an insight bundle; refusing to overwrite it")
        shutil.rmtree(to)
    manifest = read_json(run_dir / "manifest.json")
    iset = load_set(manifest["set_id"], set_root)
    if iset.sha256 != manifest["set_sha256"]:
        raise InsightError(f"set {iset.set_id} changed since the run; publish from the run's version")
    summary = relink(score(run_dir, set_root=set_root))
    data = to / "data"
    for name in COPIED:
        source = run_dir / name
        files = [source] if source.is_file() else sorted(source.rglob("*.json")) if source.is_dir() else []
        for path in files:
            doc = read_json(path)
            write_json(data / path.relative_to(run_dir), sanitize_cell(doc) if name == "cells" else doc)
    (to / "set").mkdir(parents=True)
    shutil.copy((set_root or module_root()) / SET_DIR / f"{iset.set_id}.json", to / "set")
    for arm, entry in manifest["profiles"].items():
        doc = {k: v for k, v in entry.items() if k not in ("profile",)} if entry.get("imported") else entry["profile"]
        write_json(to / "profiles" / f"{arm}.json", doc)
    if manifest.get("shared"):
        write_json(to / "profiles" / "_shared.json", manifest["shared"])
    paths = write_bundle(to, summary)
    (to / "methodology.md").write_text(_methodology(summary, manifest), encoding="utf-8")
    (to / "reproduce.md").write_text(_reproduce(summary, manifest), encoding="utf-8")
    _link_from_index(to.parent / "README.md", to, summary)
    return {**paths, "bundle": str(to), "files": sum(1 for p in to.rglob("*") if p.is_file())}
