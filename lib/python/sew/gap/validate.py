"""Author-side, offline validity checks. This is distinct from model calibration."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..catalog import module_root
from ..schema import SchemaError
from .catalog import validate_task as validate_metadata
from .verify import verify


def _signature(result, lanes):
    return tuple(
        (
            lane,
            record["passed"],
            record["exit_code"],
            record["timed_out"],
            tuple((t["id"], t["status"]) for t in record["tests"]),
        )
        for lane in lanes
        for record in result[lane]
    )


def _problem(result, *, expect_failure=False, visible_only=False):
    if result["errors"]:
        return "verifier error: " + str(result["errors"])
    if result["flakes"]:
        return "hidden tests flaked: " + ", ".join(result["flakes"])
    if not result["visible"] or not all(r["passed"] for r in result["visible"]):
        return "visible tests failed"
    if visible_only:
        return None
    if not result["hidden"]:
        return "missing hidden test results"
    for r in result["hidden"] + result["reruns"]:
        if (
            r["timed_out"]
            or r.get("report_error")
            or not r["tests"]
            or r["exit_code"] not in (0, 1)
        ):
            return "hidden test execution failed (not a task defect)"
        if any(t["status"] == "skipped" for t in r["tests"]):
            return "hidden tests skipped"
    if expect_failure:
        if result["escaped_defects"] < 1:
            return "unmodified/naive fixture did not fail a hidden task test"
        for r in result["hidden"]:
            if not r["passed"]:
                retries = [
                    x
                    for x in result["reruns"]
                    if x["initial_command_index"] == result["hidden"].index(r)
                ]
                failures = {t["id"] for t in r["tests"] if t["status"] in {"failure", "error"}}
                repeated = {
                    t["id"]
                    for x in retries
                    for t in x["tests"]
                    if t["status"] in {"failure", "error"}
                }
                if not failures or failures != repeated:
                    return "hidden failure did not reproduce on rerun"
    elif result["outcome"] != "pass":
        return "reference solution failed visible or hidden tests"
    return None


def validate_task(task: dict, *, root: Path | None = None, wheelhouse: Path | None = None) -> dict:
    """Repeat every applicable leg three times in fresh verifier sandboxes.

    Authors place a binary-safe Git patch at <hidden>/reference.diff. It changes
    only fixture files; the verifier rejects collisions with the hidden tree.
    Version selection is verifier-controlled, independent of fixture requirements.
    """
    root = Path(root or module_root()).resolve()
    record = dict(
        task_id=task.get("id"),
        kind=task.get("kind"),
        accepted=False,
        repetitions=3,
        reason=None,
        checks=[],
    )
    try:
        validate_metadata(task, root / "catalogs/gap")
        if task["task_type"] != "code":
            raise SchemaError("validity triple requires a code task; briefs use brief validation")
        reference = root / "catalogs/gap" / task["hidden"] / "reference.diff"
        if reference.is_symlink() or not reference.is_file():
            raise SchemaError("missing verifier-side reference.diff")
        if task["kind"] == "control" and any(p["role"] != "dependency" for p in task["packages"]):
            raise SchemaError("no-change controls require dependency pins only")
        legs = [("unmodified", ("dependency",), False, True)]
        if task["kind"] == "gap":
            legs = [
                ("old-visible", ("old", "dependency"), True, False),
                ("naive-bump", ("new", "dependency"), False, True),
            ]
        legs.append(("reference", ("new", "dependency"), False, False))
        signatures = {}
        with tempfile.TemporaryDirectory(prefix="sew-gap-validity-") as name:
            empty = Path(name) / "empty.diff"
            empty.write_text("")
            # Freeze the reference bytes for all repetitions.
            patch = Path(name) / "reference.diff"
            patch.write_bytes(reference.read_bytes())
            for rep in range(1, 4):
                for leg, roles, visible_only, expect_failure in legs:
                    result = verify(
                        task,
                        patch if leg == "reference" else empty,
                        root=root,
                        wheelhouse=wheelhouse,
                        install_roles=roles,
                        visible_only=visible_only,
                    )
                    record["checks"].append(dict(rep=rep, leg=leg, result=result))
                    problem = _problem(
                        result, expect_failure=expect_failure, visible_only=visible_only
                    )
                    signature = _signature(
                        result, ("visible",) if visible_only else ("visible", "hidden")
                    )
                    if leg in signatures and signature != signatures[leg]:
                        problem = "test results flaked between repetitions"
                    signatures[leg] = signature
                    if problem:
                        record["reason"] = f"{leg}, repetition {rep}: {problem}"
                        return record
        record["accepted"] = True
        record["reason"] = "all applicable checks stable across 3 repetitions"
    except (SchemaError, OSError, ValueError) as exc:
        record["reason"] = str(exc)
    return record
