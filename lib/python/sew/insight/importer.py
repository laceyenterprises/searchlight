"""Import an externally produced arm into an insight run as a graded arm.

The claims file has one JSON object per line: ``{qid, claim, source_url, ...}``.
Each question with claims becomes one ``ok`` answer-kind cell whose response is
the claims as text with their URLs, so the run's own extractor re-extracts them
and grading stays uniform: the pre-split claims are never trusted as they are.
Only the imported arm's own cells are written; every other cell is left alone.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..catalog import module_root
from .catalog import PROFILE_DIR, InsightError, load_set
from .run import RUN_VERSION, cell_path, now, read_json, write_json

_ARM_ID = re.compile(r"[a-z][a-z0-9-]{1,31}\.[a-z][a-z-]{1,23}")


def _response_text(rows: list[dict[str, Any]]) -> str:
    return "\n\n".join(f"{r['claim'].strip()}\nSource: {r['source_url'].strip()}" for r in rows)


def import_arm(
    run_dir: Path,
    *,
    arm_id: str,
    claims_path: Path,
    label: str,
    usd: float | None = None,
    note: str | None = None,
    set_root: Path | None = None,
    profile_dir: Path | None = None,
) -> dict[str, Any]:
    manifest_path = run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    if not _ARM_ID.fullmatch(arm_id):
        raise InsightError(f"arm id must be <vendor>.<operation>, got {arm_id!r}")
    folder = profile_dir or module_root() / PROFILE_DIR
    every = {path.stem for path in folder.glob("*.json") if not path.name.startswith("_")}
    frozen = manifest["profiles"].get(arm_id)
    if arm_id in every or (frozen and not frozen.get("imported")):
        raise InsightError(f"arm id {arm_id} collides with a profile; choose another")
    if not label.strip():
        raise InsightError("--label is required")
    if usd is not None and usd < 0:
        raise InsightError("--usd must be non-negative")

    raw = claims_path.read_bytes()
    iset = load_set(manifest["set_id"], set_root)
    known_qids = {q["id"] for q in iset.questions}
    by_q: dict[str, list[dict[str, Any]]] = {}
    for n, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            qid, claim, url = row["qid"], str(row["claim"]), str(row["source_url"])
        except (ValueError, KeyError, TypeError) as exc:
            raise InsightError(f"{claims_path}:{n}: needs qid, claim and source_url ({exc})") from exc
        if qid not in known_qids:
            raise InsightError(f"{claims_path}:{n}: question {qid!r} is not in set {iset.set_id}")
        if qid not in manifest["questions"]:
            raise InsightError(f"{claims_path}:{n}: question {qid!r} is not in run {manifest['run_id']}")
        if claim.strip() and url.startswith("http"):
            by_q.setdefault(qid, []).append(row)
    if not by_q:
        raise InsightError(f"{claims_path}: no usable claims")

    source_sha = hashlib.sha256(raw).hexdigest()
    basis = "reported" if usd is not None else "unknown"
    per_cell = round(usd / len(by_q), 6) if usd is not None else 0.0
    vendor, operation = arm_id.split(".")
    manifest["profiles"][arm_id] = {
        "sha256": source_sha, "imported": True, "label": label, "note": note,
        "source_sha256": source_sha, "source_file": claims_path.name, "cost_basis": basis,
        "reported_usd": usd, "imported_at": now(), "unverified": [],
        "profile": {"arm": vendor, "operation": operation, "kind": "answer", "imported": True},
    }
    # A re-import replaces only this arm's cells, including questions it no longer covers.
    for stale in (run_dir / "cells").glob(f"*/{arm_id}.json"):
        if stale.parent.name not in by_q:
            stale.unlink()
    for qid, rows in by_q.items():
        write_json(cell_path(run_dir, qid, arm_id), {
            "version": RUN_VERSION, "arm_id": arm_id, "question_id": qid, "status": "ok",
            "imported": True, "date": None, "latency_ms": None, "items": [],
            "response": _response_text(rows), "source_sha256": source_sha,
            "retrieved_via": sorted({str(r.get("retrieved_via")) for r in rows if r.get("retrieved_via")}),
            "cost": {"usd": per_cell, "basis": basis},
        })
    write_json(manifest_path, manifest)
    return {"run_dir": str(run_dir), "arm_id": arm_id, "questions": sorted(by_q),
            "claims": sum(len(r) for r in by_q.values()), "cost_basis": basis,
            "source_sha256": source_sha}
