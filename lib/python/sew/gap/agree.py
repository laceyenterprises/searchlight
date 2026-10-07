"""Add the codex agreement measurement to a GAP run graded by the primary alone.

A battery run with ``SEW_GAP_JUDGES=claude-code`` has verdicts but no judge
agreement. ``add_agreement`` passes each succeeded brief cell's stored grade to
``add_agreement_judge``, which scores codex on the payload the primary judged,
and writes the result back in place. Verdicts never change. A cell whose codex
judge fails is left unmeasured and counted, and the next pass retries it; a
cell that is already measured repairs its companion outcome if needed, without
calling the judge again.

``catalog_root`` must be the module root the run was graded from: the payload
check refuses a rubric that differs from the one the primary saw.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..grading import _identity
from ..runner import atomic_write_json
from ..schema import load_document
from .brief_grade import AgreementUnavailable, add_agreement_judge, load_brief_rubric
from .catalog import load_gap_tasks

OUTCOMES = ("gap-outcome.json", "calibration-outcome.json")


def add_agreement(run_root, *, catalog_root, judge, limit=None, progress=None) -> dict[str, Any]:
    run_root, catalog_root = Path(run_root), Path(catalog_root)
    state = load_document(run_root / "runner-state.json")
    tasks = load_gap_tasks(catalog_root)
    counts = {"measured": 0, "already_measured": 0, "not_primary_only": 0, "judge_failed": 0}
    failures = []
    for key, entry in sorted(state["entries"].items()):
        if entry.get("status") != "succeeded":
            continue
        run_dir = Path(entry["run_dir"])
        path = run_dir / "evaluations" / OUTCOMES[0]
        record = load_document(path)
        if record.get("record_version") != "gap-brief-grade-v1":
            continue  # code tasks have no judges
        agreement = record.get("agreement") or {}
        if agreement.get("status") == "measured":
            # The GAP outcome is written first. Recover an interrupted second
            # write from that saved measurement without rerunning the judge.
            companion = run_dir / "evaluations" / OUTCOMES[1]
            if not companion.exists() or load_document(companion) != record:
                atomic_write_json(companion, record)
            counts["already_measured"] += 1
            continue
        if record.get("status") != "scored" or agreement.get("reason") != "single_judge":
            counts["not_primary_only"] += 1
            continue
        if limit is not None and counts["measured"] + counts["judge_failed"] >= limit:
            break
        task = tasks[record["task_id"]]
        try:
            updated = add_agreement_judge(
                record,
                task,
                load_brief_rubric(task, catalog_root / "catalogs/gap"),
                load_document(run_dir / "artifacts/final-answer.json"),
                judge=judge,
                arm=_identity(run_dir, load_document(run_dir / "run.json")),
            )
        except AgreementUnavailable as exc:
            counts["judge_failed"] += 1
            failures.append({"cell": key, "reason": str(exc)})
            if progress:
                progress(f"{key}: codex judge failed ({exc})")
            continue
        # A battery bundle holds the same record under both names; keep them equal.
        for name in OUTCOMES:
            target = run_dir / "evaluations" / name
            if target.exists() and load_document(target) == record:
                atomic_write_json(target, updated)
        counts["measured"] += 1
        if progress:
            dispute = " DISPUTED" if updated["agreement"]["verdict_disputed"] else ""
            progress(
                f"{key}: kappa {updated['agreement']['cohens_kappa']}, "
                f"{len(updated['agreement']['disagreements'])} label disagreements{dispute}"
            )
    return {**counts, "failures": failures}
