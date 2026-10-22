"""Attest the catalog and recover checkpoints from pre-checkpoint bundles."""

import fcntl
import os
from pathlib import Path
import re

from ..catalog import module_root
from ..runner import atomic_write_json
from ..schema import SchemaError, load_document
from ..state import default_state_root
from .calibrate import (
    CalibrationError,
    calibration_cell,
    calibration_checkpoint,
    calibration_path,
    catalog_digest,
    cell_config,
)
from .catalog import load_gap_tasks


CELL_NAME = re.compile(r"(.+)-(floor|ceiling)-([1-9][0-9]*)(?:-att([1-9][0-9]*))?")


def _document(path):
    try:
        document = load_document(path)
        return document if isinstance(document, dict) else {}
    except (SchemaError, OSError):
        return {}


def _cell(attempts, *, harness, model, task_id, arm, rep, digest):
    excluded = []
    candidates = []
    reasons = []
    for directory in attempts:
        run = _document(directory / "run.json")
        metadata = _document(directory / "artifacts/spawn-metadata.json")
        audit = metadata.get("arm_audit")
        audit = audit if isinstance(audit, dict) else {}
        contaminated = (
            run.get("status") == "contaminated" or audit.get("contaminated") is True
        )
        if contaminated:
            excluded.append(str(directory))
        reason = None
        for key, expected in (
            ("harness_id", harness),
            ("task_id", task_id),
            ("provider_id", arm),
        ):
            if run.get(key) != expected:
                reason = f"{key} mismatch or missing"
                break
        if reason is None:
            if metadata.get("model_id") != model:
                reason = "model_id mismatch or missing"
            elif contaminated:
                reason = "contaminated attempt"
            elif run.get("status") != "succeeded":
                reason = "status is not succeeded"
            elif audit.get("contaminated") is not False:
                reason = "arm_audit.contaminated must be exactly false"
        if reason:
            reasons.append(f"{directory.name}: {reason}")
        else:
            candidates.append((directory, audit))
    if not candidates:
        return None, "; ".join(reasons)
    # Numeric attempt order is stable even when retries exceed nine attempts.
    directory, audit = candidates[0]
    cell = calibration_cell(
        rep,
        directory,
        [str(p) for p in attempts],
        excluded,
        audit.get("denied_network_attempts", 0),
        audit.get("config_neutralized_network_attempts", 0),
    )
    cell.update(backfilled=True, catalog_hash=digest)
    grade = _document(directory / "evaluations/calibration-outcome.json")
    if grade.get("outcome") in ("pass", "fail"):
        cell["outcome"] = grade["outcome"]
    return cell, None


def backfill(
    run_id, *, harness, model, catalog_hash, dry_run=False, state_root=None, root=None
):
    """Return the write count and skip reasons; dry-run projects the same plan."""
    state_root = default_state_root() if state_root is None else Path(state_root)
    runs_root = (
        calibration_path(state_root, harness, model).parent.parent / "calibration-runs"
    )
    root = Path(root or module_root()).resolve()
    digest = catalog_digest(root)
    if catalog_hash != digest:
        raise CalibrationError("--catalog-hash must equal the current catalog SHA256")
    tasks = load_gap_tasks(root)
    if not isinstance(run_id, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]*", run_id
    ):
        raise CalibrationError("RUN_ID must name a calibration run")
    directory = runs_root / run_id
    if not directory.is_dir() or directory.is_symlink():
        raise CalibrationError(f"calibration run not found: {run_id}")
    # Serialize backfill invocations without creating state, including dry-run.
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if any(directory.glob("*.calibration.json")):
            raise CalibrationError(
                "run already has a calibration checkpoint; refusing backfill"
            )
        groups = {}
        skipped = []
        for attempt in sorted(directory.iterdir()):
            if not attempt.is_dir():
                continue
            match = CELL_NAME.fullmatch(attempt.name)
            if match is None:
                skipped.append(
                    {"cell": attempt.name, "reason": "invalid cell directory name"}
                )
                continue
            task_id, arm, rep, retry = match.groups()
            key = (task_id, arm, int(rep))
            groups.setdefault(key, []).append((int(retry or 1), attempt))
        planned = []
        for (task_id, arm, rep), attempts in sorted(groups.items()):
            name = f"{task_id}-{arm}-{rep}"
            reason = (
                "unknown task id in current catalog" if task_id not in tasks else None
            )
            cell = None
            if reason is None:
                cell, reason = _cell(
                    [p for _, p in sorted(attempts)],
                    harness=harness,
                    model=model,
                    task_id=task_id,
                    arm=arm,
                    rep=rep,
                    digest=digest,
                )
            if cell is None:
                skipped.append({"cell": name, "reason": reason})
                continue
            matching = cell_config(harness, model, task_id, arm, rep, digest)
            planned.append(
                (
                    directory / f"{name}.calibration.json",
                    calibration_checkpoint(matching, cell),
                )
            )
        if catalog_digest(root) != digest:
            raise CalibrationError("catalog changed during backfill")
        if not dry_run:
            for path, checkpoint in planned:
                atomic_write_json(path, checkpoint)
        return {
            "checkpoints_written": len(planned),
            "skipped": skipped,
            "dry_run": dry_run,
        }
    finally:
        os.close(descriptor)


def render_backfill(summary):
    action = "would write (dry-run)" if summary["dry_run"] else "written"
    lines = [
        f"Checkpoints {action}: {summary['checkpoints_written']}",
        f"Cells skipped: {len(summary['skipped'])}",
    ]
    lines.extend(f"  {cell['cell']}: {cell['reason']}" for cell in summary["skipped"])
    return "\n".join(lines)
