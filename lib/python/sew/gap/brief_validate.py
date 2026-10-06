"""Author-side brief validity pair; model admission remains calibration's job."""

from __future__ import annotations

import json
from pathlib import Path

from ..catalog import module_root
from ..schema import SchemaError
from .brief_grade import grade_brief, load_brief_rubric
from .catalog import validate_task


def validate_brief_task(task, *, judges, capture_source, root: Path | None = None):
    """Reject unless a reference passes and a well-formed stale answer fails.

    Judges and source capture are injected exactly as for grade_brief. CI uses
    content-checking fixture judges and frozen evidence; live callers can use
    the two real judges. Neither unavailable judges nor malformed stale JSON
    constitute evidence of a knowledge gap. No model or provider is invoked
    implicitly, and no state is written into the catalog.
    """
    base = (Path(root or module_root()) / "catalogs/gap").resolve()
    record = {"task_id": task.get("id"), "accepted": False, "checks": {}, "reason": None}
    try:
        validate_task(task, base)
        if task["task_type"] != "brief" or task["kind"] != "gap":
            raise SchemaError("brief validity requires a gap brief task")
        rubric = load_brief_rubric(task, base)
        hidden = (base / task["hidden"]).resolve()
        for leg in ("reference", "stale"):
            path = hidden / f"{leg}.json"
            if path.is_symlink() or not path.is_file():
                raise SchemaError(f"missing verifier-side {leg}.json")
            answer = json.loads(path.read_text(encoding="utf-8"))
            result = grade_brief(task, rubric, answer, judges=judges, capture_source=capture_source)
            record["checks"][leg] = result
            if result["status"] != "scored":
                record["reason"] = f"{leg} ungraded: {result['status']}"
                return record
            if result["agreement"]["verdict_disputed"]:
                record["reason"] = f"{leg} judges disagree on validity verdict"
                return record
            if result["passed"] != (leg == "reference"):
                record["reason"] = f"{leg} brief {'passes' if result['passed'] else 'fails'}"
                return record
        record.update(accepted=True, reason="reference passes and stale knowledge fails")
    except (SchemaError, OSError, ValueError) as exc:
        record["reason"] = str(exc)
    return record
